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
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';
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

const SENSITIVE_METADATA_KEY =
  /authorization|cookie|password|passwd|secret|session|token|api[-_]?key/i;

/** Defense in depth: preserve useful metadata while hiding sensitive values. */
function sanitizeAuditMetadata(value: unknown, key = ''): unknown {
  if (SENSITIVE_METADATA_KEY.test(key)) return '[REDACTED]';
  if (Array.isArray(value)) return value.map((item) => sanitizeAuditMetadata(item));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([entryKey, entryValue]) => [
        entryKey,
        sanitizeAuditMetadata(entryValue, entryKey),
      ])
    );
  }
  return value;
}

function formatAuditMetadata(metadata: Record<string, unknown> | null | undefined): string {
  if (!metadata) return '{}';
  return JSON.stringify(sanitizeAuditMetadata(metadata), null, 2) ?? '{}';
}

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
    <Card role="region" aria-label="Security overview, exact trailing 24 hours" className="p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden />
            <h2 className="text-sm font-semibold">Security</h2>
            <Badge variant="neutral" aria-label="Window: exact trailing 24 hours">
              exact trailing {data?.windowHours ?? 24}h
            </Badge>
            {data && (
              <span className="text-xs text-[var(--muted-foreground)]">
                As of <LocalTime iso={data.computedAt} />
              </span>
            )}
          </div>
          {data && (
            <div className="mt-1 text-xs text-[var(--muted-foreground)]">
              <p>
                Window <LocalTime iso={data.windowStart} /> to <LocalTime iso={data.windowEnd} /> (
                {data.windowKind.replaceAll('_', ' ')}, end exclusive).
              </p>
              <p>
                Admin-login role basis:{' '}
                {data.adminLoginRoleBasis === 'current_role_at_query_time'
                  ? 'current role at query time'
                  : data.adminLoginRoleBasis.replaceAll('_', ' ')}
                .
              </p>
            </div>
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
              label="CAPTCHA denied"
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
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';

  const [eventInput, setEventInput] = React.useState(event);
  const [cursorStack, setCursorStack] = React.useState<string[]>([]);
  const [expandedRows, setExpandedRows] = React.useState<Set<string>>(() => new Set());
  const cursor = cursorStack[cursorStack.length - 1] ?? null;

  const toggleRow = (id: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  React.useEffect(() => {
    setCursorStack([]);
  }, [event, actor, target, from, to]);

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
    from: from || undefined,
    to: to || undefined,
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
          Append-only trail of security-relevant actions and sensitive reads. Entries are retained
          according to configured retention; older rows may be removed.
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
        {(actor || target || from || to) && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted-foreground)]">
            {actor && <Badge variant="ai">actor: {actor.slice(0, 8)}...</Badge>}
            {target && <Badge variant="ai">target: {target.slice(0, 8)}...</Badge>}
            {from && <Badge variant="neutral">from: {from}</Badge>}
            {to && <Badge variant="neutral">to: {to}</Badge>}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setParam({ actor: null, target: null, from: null, to: null })}
            >
              Clear
            </Button>
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label
              htmlFor="audit-from"
              className="mb-1 block text-xs text-[var(--muted-foreground)]"
            >
              From date
            </label>
            <Input
              id="audit-from"
              type="date"
              value={from}
              max={to || undefined}
              onChange={(e) => setParam({ from: e.target.value || null })}
              className="w-auto"
            />
          </div>
          <div>
            <label htmlFor="audit-to" className="mb-1 block text-xs text-[var(--muted-foreground)]">
              To date
            </label>
            <Input
              id="audit-to"
              type="date"
              value={to}
              min={from || undefined}
              onChange={(e) => setParam({ to: e.target.value || null })}
              className="w-auto"
            />
          </div>
        </div>
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
                    <TableHead className="w-20 text-right">Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((a) => {
                    const expanded = expandedRows.has(a.id);
                    const detailsId = `audit-details-${a.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

                    return (
                      <React.Fragment key={a.id}>
                        <TableRow>
                          <TableCell className="whitespace-nowrap py-2">
                            <LocalTime iso={a.ts} />
                          </TableCell>
                          <TableCell className="py-2 font-mono text-xs">{a.event}</TableCell>
                          <TableCell className="py-2 font-mono text-xs text-[var(--muted-foreground)]">
                            {a.actorUserId ? a.actorUserId.slice(0, 8) + '...' : '-'}
                          </TableCell>
                          <TableCell className="py-2 font-mono text-xs text-[var(--muted-foreground)]">
                            {a.targetUserId ? a.targetUserId.slice(0, 8) + '...' : '-'}
                          </TableCell>
                          <TableCell className="py-1 text-right">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-expanded={expanded}
                              aria-controls={detailsId}
                              aria-label={`${expanded ? 'Collapse' : 'Expand'} details for ${a.event}`}
                              onClick={() => toggleRow(a.id)}
                            >
                              <ChevronDown
                                className={`h-4 w-4 transition-transform ${expanded ? 'rotate-180' : ''}`}
                                aria-hidden
                              />
                              <span className="sr-only">{expanded ? 'Collapse' : 'Expand'}</span>
                            </Button>
                          </TableCell>
                        </TableRow>
                        {expanded && (
                          <TableRow id={detailsId}>
                            <TableCell colSpan={5} className="bg-[var(--muted)]/30 p-4">
                              <dl className="grid gap-3 text-xs sm:grid-cols-2">
                                <div>
                                  <dt className="font-medium text-[var(--muted-foreground)]">
                                    Actor ID
                                  </dt>
                                  <dd className="break-all font-mono">{a.actorUserId ?? '-'}</dd>
                                </div>
                                <div>
                                  <dt className="font-medium text-[var(--muted-foreground)]">
                                    Target ID
                                  </dt>
                                  <dd className="break-all font-mono">{a.targetUserId ?? '-'}</dd>
                                </div>
                                <div>
                                  <dt className="font-medium text-[var(--muted-foreground)]">
                                    Request ID
                                  </dt>
                                  <dd className="break-all font-mono">{a.requestId ?? '-'}</dd>
                                </div>
                                <div>
                                  <dt className="font-medium text-[var(--muted-foreground)]">
                                    IP hash
                                  </dt>
                                  <dd className="break-all font-mono">{a.ipHash ?? '-'}</dd>
                                </div>
                                <div className="sm:col-span-2">
                                  <dt className="mb-1 font-medium text-[var(--muted-foreground)]">
                                    Metadata
                                  </dt>
                                  <dd>
                                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-3 font-mono text-xs">
                                      {formatAuditMetadata(a.meta)}
                                    </pre>
                                  </dd>
                                </div>
                              </dl>
                            </TableCell>
                          </TableRow>
                        )}
                      </React.Fragment>
                    );
                  })}
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
