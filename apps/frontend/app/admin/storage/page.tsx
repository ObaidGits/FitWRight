'use client';

/**
 * Admin Storage (Task 12.3 / Req 7, 11.2, 11.4).
 *
 * Surfaces the cached storage snapshot the backend `StorageService` samples
 * periodically (never a live, request-time size query): an approximate database
 * size and object-storage usage (each optionally from a stale sample), the
 * resource counts (avatars / resumes / resume versions), a coarse retention
 * status, and an estimated daily growth.
 *
 * Every optional/absent field renders an explicit indicator, never a blank or a
 * misleading zero (Req 7):
 *  - a size that is `null` shows "Unavailable"; a stale sample adds a "Stale"
 *    badge (text + color, never color alone — a11y);
 *  - when `growthUnavailable`, the growth card shows "Unavailable" with the
 *    backend-provided reason instead of a fabricated rate.
 *
 * On fetch failure it shows an explicit error state with a working retry control
 * (Req 11.4); loading shows a skeleton. Results are announced via `aria-live`
 * without stealing focus, and "As of <computedAt>" renders in local time with a
 * UTC tooltip.
 */
import * as React from 'react';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import Database from 'lucide-react/dist/esm/icons/database';
import HardDrive from 'lucide-react/dist/esm/icons/hard-drive';
import TrendingUp from 'lucide-react/dist/esm/icons/trending-up';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import { LocalTime } from '@/components/admin/local-time';
import { useStorage } from '@/features/admin/hooks';
import type { StoragePanel } from '@/lib/api/admin';

// ---------------------------------------------------------------------------
// Byte formatting — a small local, null-safe helper (binary units, 1 KB = 1024
// bytes) so a sampled size renders as "1.2 GB". Returns `null` for absent values
// so the caller renders an explicit "Unavailable" indicator rather than a 0.
// ---------------------------------------------------------------------------

const BYTE_UNITS = ['bytes', 'KB', 'MB', 'GB', 'TB', 'PB'] as const;

/** `1288490188 → "1.2 GB"`; `0 → "0 bytes"`; `null`/negative/NaN → `null`. */
function formatBytes(bytes?: number | null): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes === 0) return '0 bytes';
  const k = 1024;
  const i = Math.min(BYTE_UNITS.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
  const value = bytes / k ** i;
  // Whole bytes have no fraction; larger units keep up to 2 significant decimals.
  const digits = i === 0 ? 0 : value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${BYTE_UNITS[i]}`;
}

/** `bytes/day → "1.2 GB / day"`; `null` when the estimate is unavailable. */
function formatGrowth(bytesPerDay?: number | null): string | null {
  const formatted = formatBytes(bytesPerDay);
  return formatted == null ? null : `${formatted} / day`;
}

// ---------------------------------------------------------------------------
// Presentational pieces
// ---------------------------------------------------------------------------

/** An explicit "Unavailable" badge (text + color) — never a blank or fake 0. */
function UnavailableBadge({ label }: { label: string }) {
  return (
    <Badge variant="outline" aria-label={`${label}: unavailable`}>
      Unavailable
    </Badge>
  );
}

/**
 * One size card: a human-readable byte value, a "Stale" badge when the sample is
 * stale, or an explicit "Unavailable" badge when there is no sample at all.
 */
function SizeCard({
  label,
  icon: Icon,
  bytes,
  stale,
  hint,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  bytes?: number | null;
  stale: boolean;
  hint?: string;
}) {
  const formatted = formatBytes(bytes);
  const unavailable = formatted == null;
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden />
          <p className="text-sm text-[var(--muted-foreground)]">{label}</p>
        </div>
        {/* Stale is only meaningful when we actually have a (possibly old) sample. */}
        {!unavailable && stale && (
          <Badge variant="warning" aria-label={`${label} sample may be stale`}>
            Stale
          </Badge>
        )}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">
        {unavailable ? <UnavailableBadge label={label} /> : formatted}
      </p>
      {hint && <p className="mt-1 text-xs text-[var(--muted-foreground)]">{hint}</p>}
    </Card>
  );
}

/** One count card: a formatted integer resource count. */
function CountCard({ label, value }: { label: string; value: number }) {
  return (
    <Card className="p-5">
      <p className="text-sm text-[var(--muted-foreground)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
    </Card>
  );
}

/** Estimated growth card: a bytes/day rate, or "Unavailable" with the reason. */
function GrowthCard({ data }: { data: StoragePanel }) {
  const formatted = data.growthUnavailable ? null : formatGrowth(data.growthBytesPerDay);
  const unavailable = data.growthUnavailable || formatted == null;
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2">
        <TrendingUp className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden />
        <p className="text-sm text-[var(--muted-foreground)]">Estimated growth</p>
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">
        {unavailable ? <UnavailableBadge label="Estimated growth" /> : formatted}
      </p>
      {unavailable ? (
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          {data.growthUnavailableReason || 'Insufficient samples to estimate growth.'}
        </p>
      ) : (
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          Based on recent sampled sizes.
        </p>
      )}
    </Card>
  );
}

/** Retention status: a coarse text label, or an explicit "Unknown" indicator. */
function RetentionCard({ status }: { status?: string | null }) {
  const has = !!status && status.trim().length > 0;
  return (
    <Card className="p-5">
      <p className="text-sm text-[var(--muted-foreground)]">Retention status</p>
      <div className="mt-2">
        {has ? (
          <span className="text-lg font-semibold">{status}</span>
        ) : (
          <Badge variant="outline" aria-label="Retention status: unknown">
            Unknown
          </Badge>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminStoragePage() {
  const { data, isLoading, isError, error, isFetching, refetch } = useStorage();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Storage</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Database and object-storage usage, resource counts and estimated growth.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {/* aria-live so async results are announced without stealing focus. */}
      <div aria-live="polite" className="space-y-6">
        {isError ? (
          <ErrorState
            title="Couldn't load storage"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading || !data ? (
          <LoadingSkeleton rows={3} />
        ) : (
          <>
            <p className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
              As of <LocalTime iso={data.computedAt} />
            </p>

            {/* Sampled sizes — DB + object storage (stale/unavailable aware). */}
            <section aria-label="Storage usage">
              <div className="grid gap-4 sm:grid-cols-2">
                <SizeCard
                  label="Database size"
                  icon={Database}
                  bytes={data.dbSizeBytes}
                  stale={data.dbSizeStale}
                  hint="Approximate, from a periodic sample."
                />
                <SizeCard
                  label="Object storage"
                  icon={HardDrive}
                  bytes={data.objectStorageBytes}
                  stale={data.objectStorageStale}
                  hint="Uploaded files (avatars, resume assets)."
                />
              </div>
            </section>

            {/* Resource counts. */}
            <section aria-label="Resource counts">
              <div className="grid gap-4 sm:grid-cols-3">
                <CountCard label="Avatars" value={data.avatarCount} />
                <CountCard label="Resumes" value={data.resumeCount} />
                <CountCard label="Resume versions" value={data.resumeVersionCount} />
              </div>
            </section>

            {/* Growth estimate + retention status. */}
            <section aria-label="Growth and retention">
              <div className="grid gap-4 sm:grid-cols-2">
                <GrowthCard data={data} />
                <RetentionCard status={data.retentionStatus} />
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
