'use client';

/**
 * Admin audit view (Task 8.2) - cursor-paginated, filterable, append-only.
 *
 * The audit log is read-only (no mutate API); this page only filters + paginates
 * it. Filters (event/actor/target) are URL-synced. Long lists are page-bounded
 * (virtualization-friendly) - we render a bounded page and paginate by cursor.
 */
import * as React from 'react';
import { Suspense } from 'react';
import { useRouter, usePathname, useSearchParams } from 'next/navigation';
import ScrollText from 'lucide-react/dist/esm/icons/scroll-text';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import ShieldAlert from 'lucide-react/dist/esm/icons/shield-alert';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { LoadingSkeleton, EmptyState, ErrorState } from '@/components/atelier/states';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/atelier/table';
import { LocalTime } from '@/components/admin/local-time';
import { useAdminAudit, useSecurity } from '@/features/admin/hooks';
import type { AuditListParams } from '@/lib/api/admin';

// Req 11.8: every list view paginates with the shared cursor pagination at a
// page size of 25.
const PAGE_SIZE = 25;

// ---------------------------------------------------------------------------
// Security strip (Req 9 / task 13.3) - a compact, self-contained row of the
// trailing-24h security counts, surfaced here because the Audit log is the
// natural security-adjacent home. It owns its OWN observability query
// (`useSecurity`) so it loads, errors and refreshes independently of the audit
// list: on error it shows a small inline message with a retry control and never
// blocks the list below. Each count is authoritative TEXT (label + number);
// where a non-zero count is highlighted, color is paired with a text label so
// status is never signalled by color alone (a11y). The strip wraps on mobile.
// ---------------------------------------------------------------------------

/** One security stat tile: a text label + a text count, optionally highlighted. */
function SecurityStat({
  label,
  value,
  highlight,
  notInstrumented,
}: {
  label: string;
  value: number;
  /** When true AND the count is non-zero, draw attention with color + text. */
  highlight?: boolean;
  /** When true, this signal has no durable source - show an explicit
   *  "Not instrumented" indicator instead of a misleading 0. */
  notInstrumented?: boolean;
}) {
  const alert = !!highlight && value > 0 && !notInstrumented;
  return (
    <div
      className="min-w-[7rem] flex-1 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3"
      // The count itself is readable text; color is supplementary, never the
      // sole signal (the label + number are always present).
    >
      <p className="text-xs text-[var(--muted-foreground)]">{label}</p>
      {notInstrumented ? (
        <p
          className="mt-1 text-xs font-medium text-[var(--muted-foreground)]"
          title="No durable metric source - not instrumented"
        >
          Not instrumented
        </p>
      ) : (
        <p
          className={`mt-1 text-xl font-semibold tabular-nums ${
            alert ? 'text-[var(--destructive)]' : 'text-[var(--foreground)]'
          }`}
        >
          {value.toLocaleString()}
        </p>
      )}
    </div>
  );
}

function SecurityStrip() {
  const security = useSecurity();
  const data = security.data;

  return (
    <Card role="region" aria-label="Security overview, last 24 hours" className="p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden />
          <h2 className="text-sm font-semibold">Security</h2>
          <Badge variant="neutral" aria-label={`Window: last ${data?.windowHours ?? 24} hours`}>
            last {data?.windowHours ?? 24}h
          </Badge>
          {data && (
            <span className="text-xs text-[var(--muted-foreground)]">
              As of <LocalTime iso={data.computedAt} />
            </span>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => security.refetch()}
          disabled={security.isFetching}
          aria-label="Refresh security metrics"
        >
          <RefreshCw className={`h-4 w-4 ${security.isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {/* aria-live so the counts are announced when the async fetch resolves. */}
      <div aria-live="polite">
        {security.isError ? (
          // Compact inline error - deliberately NOT the full-page ErrorState, so
          // a security-metrics failure never blocks the audit list below.
          <div
            role="alert"
            className="flex flex-wrap items-center gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-3 text-sm text-[var(--muted-foreground)]"
          >
            <span>Couldn&apos;t load security metrics.</span>
            <Button variant="outline" size="sm" onClick={() => security.refetch()}>
              Try again
            </Button>
          </div>
        ) : security.isLoading || !data ? (
          <LoadingSkeleton rows={1} />
        ) : (
          <div className="flex flex-wrap gap-3">
            <SecurityStat label="Failed logins" value={data.loginFailed} highlight />
            <SecurityStat label="Admin logins" value={data.adminLogin} />
            <SecurityStat label="Authz denied" value={data.authzDenied} highlight />
            <SecurityStat
              label="Rate-limited"
              value={data.rateLimited}
              highlight
              notInstrumented={data.notInstrumented?.includes('rateLimited')}
            />
            <SecurityStat
              label="Suspicious"
              value={data.suspicious}
              highlight
              notInstrumented={data.notInstrumented?.includes('suspicious')}
            />
          </div>
        )}
      </div>
    </Card>
  );
}

export default function AdminAuditPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={6} />}>
      <AdminAuditPageInner />
    </Suspense>
  );
}

function AdminAuditPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const event = params.get('event') ?? '';
  const actor = params.get('actor') ?? '';
  const target = params.get('target') ?? '';

  const [eventInput, setEventInput] = React.useState(event);
  const [cursorStack, setCursorStack] = React.useState<string[]>([]);
  const cursor = cursorStack[cursorStack.length - 1] ?? null;

  React.useEffect(() => {
    setCursorStack([]);
  }, [event, actor, target]);

  const setParam = (patch: Record<string, string | null>) => {
    const sp = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (!v) sp.delete(k);
      else sp.set(k, v);
    }
    router.replace(sp.toString() ? `${pathname}?${sp.toString()}` : pathname);
  };

  React.useEffect(() => {
    const t = setTimeout(() => {
      if (eventInput !== event) setParam({ event: eventInput || null });
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventInput]);

  const listParams: AuditListParams = {
    event: event || undefined,
    actor: actor || undefined,
    target: target || undefined,
    cursor,
    limit: PAGE_SIZE,
  };
  const { data, isLoading, isError, error, refetch, isFetching } = useAdminAudit(listParams);
  const rows = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Audit log</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Append-only trail of security-relevant actions and sensitive reads.
        </p>
      </div>

      {/* Compact trailing-24h security counts (Req 9 / task 13.3). Owns its own
          query + loading/error/retry; a failure here never blocks the list. */}
      <SecurityStrip />

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={eventInput}
          onChange={(e) => setEventInput(e.target.value)}
          placeholder="Filter by event (e.g. user.disabled)..."
          className="max-w-xs"
          aria-label="Filter by event"
        />
        {(actor || target) && (
          <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
            {actor && <Badge variant="ai">actor: {actor.slice(0, 8)}...</Badge>}
            {target && <Badge variant="ai">target: {target.slice(0, 8)}...</Badge>}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setParam({ actor: null, target: null })}
            >
              Clear
            </Button>
          </div>
        )}
      </div>

      {/* aria-live so async list results are announced without stealing focus. */}
      <div aria-live="polite" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            title="Couldn't load audit log"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading ? (
          <LoadingSkeleton rows={6} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="No audit entries"
            description="No events match the current filter."
          />
        ) : (
          <>
            <Card className="overflow-hidden p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Event</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>Target</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="whitespace-nowrap">
                        <LocalTime iso={a.ts} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{a.event}</TableCell>
                      <TableCell className="font-mono text-xs text-[var(--muted-foreground)]">
                        {a.actorUserId ? a.actorUserId.slice(0, 8) + '...' : '-'}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-[var(--muted-foreground)]">
                        {a.targetUserId ? a.targetUserId.slice(0, 8) + '...' : '-'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
            <div className="flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={cursorStack.length === 0 || isFetching}
                onClick={() => setCursorStack((s) => s.slice(0, -1))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!data?.nextCursor || isFetching}
                onClick={() => data?.nextCursor && setCursorStack((s) => [...s, data.nextCursor!])}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
