'use client';

import * as React from 'react';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atelier/table';
import { Sheet, SheetContent, SheetTitle } from '@/components/atelier/sheet';
import { LocalTime } from '@/components/admin/local-time';
import { useAdminErrorReports } from '@/features/admin/hooks';
import type { AdminErrorReport, ErrorReportListParams } from '@/lib/api/admin';

const PAGE_SIZE = 25;

function valueOrDash(value: string | number | boolean | null | undefined): React.ReactNode {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

function DetailField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-3 border-b border-[var(--border)] py-2 last:border-0">
      <dt className="text-[var(--muted-foreground)]">{label}</dt>
      <dd className={mono ? 'break-all font-mono text-xs' : 'break-words'}>{value}</dd>
    </div>
  );
}

function ReportDetail({
  report,
  onClose,
}: {
  report: AdminErrorReport | null;
  onClose: () => void;
}) {
  return (
    <Sheet open={!!report} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full max-w-lg overflow-y-auto p-6">
        <SheetTitle className="text-lg font-semibold">Error report details</SheetTitle>
        {report && (
          <div className="mt-4 space-y-5 text-sm">
            <div>
              <p className="font-medium">{report.user.name}</p>
              <p className="text-[var(--muted-foreground)]">{report.user.email}</p>
            </div>
            <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Safe error message
              </p>
              <p className="mt-1 break-words">{report.message}</p>
            </div>
            <dl>
              <DetailField label="Reported" value={<LocalTime iso={report.createdAt} />} />
              <DetailField label="Issue" value={report.issueType} mono />
              <DetailField label="Error code" value={valueOrDash(report.errorCode)} mono />
              <DetailField label="HTTP status" value={valueOrDash(report.httpStatus)} />
              <DetailField label="Retryable" value={valueOrDash(report.retryable)} />
              <DetailField label="API method" value={report.apiMethod} mono />
              <DetailField label="API route" value={report.apiRoute} mono />
              <DetailField label="Pipeline stage" value={valueOrDash(report.pipelineStage)} />
              <DetailField label="Stream phase" value={valueOrDash(report.streamPhase)} />
              <DetailField label="Fallback safe" value={valueOrDash(report.fallbackSafe)} />
              <DetailField label="API request ID" value={valueOrDash(report.apiRequestId)} mono />
              <DetailField
                label="Operation ID"
                value={valueOrDash(report.operationRequestId)}
                mono
              />
              <DetailField label="Client report ID" value={report.clientReportId} mono />
              <DetailField label="Report ID" value={report.id} mono />
              <DetailField label="User ID" value={report.user.id} mono />
            </dl>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default function AdminErrorReportsPage() {
  const [cursorStack, setCursorStack] = React.useState<string[]>([]);
  const [selected, setSelected] = React.useState<AdminErrorReport | null>(null);
  const cursor = cursorStack[cursorStack.length - 1] ?? null;
  const params: ErrorReportListParams = { cursor, limit: PAGE_SIZE };
  const { data, isLoading, isError, error, refetch, isFetching } = useAdminErrorReports(params);
  const reports = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Error reports</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Privacy-minimized Tailor failures submitted by users.
        </p>
      </div>

      <div aria-live="polite" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            title="Couldn't load error reports"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading ? (
          <LoadingSkeleton rows={5} />
        ) : reports.length === 0 ? (
          <EmptyState
            icon={TriangleAlert}
            title="No error reports"
            description="User-submitted Tailor failures will appear here."
          />
        ) : (
          <>
            <Card className="hidden overflow-hidden p-0 md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Time</TableHead>
                    <TableHead>Issue / error</TableHead>
                    <TableHead>API route / status</TableHead>
                    <TableHead>Stage</TableHead>
                    <TableHead>Correlation IDs</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {reports.map((report) => (
                    <TableRow key={report.id}>
                      <TableCell>
                        <p className="font-medium">{report.user.name}</p>
                        <p className="max-w-44 truncate text-xs text-[var(--muted-foreground)]">
                          {report.user.email}
                        </p>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-[var(--muted-foreground)]">
                        <LocalTime iso={report.createdAt} />
                      </TableCell>
                      <TableCell>
                        <p className="font-mono text-xs">{report.errorCode ?? report.issueType}</p>
                        <Badge variant={report.retryable ? 'ai' : 'danger'}>
                          {report.retryable ? 'retryable' : 'not retryable'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <p className="max-w-52 truncate font-mono text-xs">{report.apiRoute}</p>
                        <p className="text-xs text-[var(--muted-foreground)]">
                          {report.apiMethod} · {report.httpStatus ?? 'no status'}
                        </p>
                      </TableCell>
                      <TableCell>{report.pipelineStage ?? '—'}</TableCell>
                      <TableCell className="max-w-48">
                        <p className="truncate font-mono text-xs" title={report.apiRequestId ?? ''}>
                          API: {report.apiRequestId ?? '—'}
                        </p>
                        <p
                          className="truncate font-mono text-xs text-[var(--muted-foreground)]"
                          title={report.operationRequestId ?? ''}
                        >
                          Operation: {report.operationRequestId ?? '—'}
                        </p>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button size="sm" variant="ghost" onClick={() => setSelected(report)}>
                          View
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>

            <div className="space-y-3 md:hidden">
              {reports.map((report) => (
                <Card key={report.id} className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{report.user.name}</p>
                      <p className="truncate text-sm text-[var(--muted-foreground)]">
                        {report.user.email}
                      </p>
                    </div>
                    <Badge variant={report.retryable ? 'ai' : 'danger'}>
                      {report.httpStatus ?? 'network'}
                    </Badge>
                  </div>
                  <p className="mt-3 truncate font-mono text-xs">{report.apiRoute}</p>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span className="text-xs text-[var(--muted-foreground)]">
                      <LocalTime iso={report.createdAt} /> · {report.pipelineStage ?? 'no stage'}
                    </span>
                    <Button size="sm" variant="outline" onClick={() => setSelected(report)}>
                      View
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <div className="mt-4 flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={cursorStack.length === 0 || isFetching}
                onClick={() => setCursorStack((stack) => stack.slice(0, -1))}
              >
                Previous
              </Button>
              <span className="text-xs text-[var(--muted-foreground)]">
                {isFetching ? 'Loading...' : `${reports.length} shown`}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={!data?.nextCursor || isFetching}
                onClick={() =>
                  data?.nextCursor &&
                  setCursorStack((stack) => [...stack, data.nextCursor as string])
                }
              >
                Next
              </Button>
            </div>
          </>
        )}
      </div>

      <ReportDetail report={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
