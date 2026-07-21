/**
 * Version-history API (P3 §A, Requirements 1-3) - wired to the real backend.
 *
 * Snapshots are captured server-side on meaningful changes (initial parse ->
 * `original`, accepted AI generation -> `ai`, manual save -> `manual`) with
 * content-hash dedupe, gzip storage, and a per-resume cap. This module exposes
 * the typed operations the `VersionHistoryPanel` (and future diff viewer) use:
 * list metadata, fetch one snapshot's data on demand, restore (non-destructive),
 * undo-last-ai, and compare.
 *
 * The list is metadata-only; the (decompressed) payload is fetched lazily via
 * {@link getVersion}. When the `VERSION_HISTORY` flag is off the backend 404s
 * the whole surface, which surfaces here as an empty history (never an error
 * toast on open).
 */
import type { ResumeVersion } from '@/lib/types/domain';

import { apiFetch, apiPost } from './client';

// ---- wire types (snake_case backend contract) -----------------------------

interface RawVersion {
  id: string;
  resume_id: string;
  source: 'original' | 'ai' | 'manual';
  label: string | null;
  content_hash: string;
  size_bytes: number;
  created_at: string;
}

interface RawVersionList {
  items: RawVersion[];
  next_cursor: string | null;
}

export interface VersionDiffEntry {
  path: string;
  action: 'added' | 'removed' | 'changed';
  before: unknown;
  after: unknown;
}

export interface VersionCompare {
  a: ResumeVersion;
  b: ResumeVersion;
  changes: VersionDiffEntry[];
}

export interface VersionWithData extends ResumeVersion {
  sizeBytes: number;
  processedData: Record<string, unknown>;
}

// ---- mapping + error helpers ----------------------------------------------

function defaultLabel(source: RawVersion['source']): string {
  switch (source) {
    case 'original':
      return 'Original parsed resume';
    case 'ai':
      return 'AI generation';
    default:
      return 'Manual save';
  }
}

function mapVersion(r: RawVersion): ResumeVersion {
  return {
    id: r.id,
    resumeId: r.resume_id,
    label: r.label ?? defaultLabel(r.source),
    source: r.source,
    createdAt: r.created_at,
  };
}

function extractDetail(data: unknown): string | null {
  if (data && typeof data === 'object') {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
  }
  return null;
}

async function asJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(extractDetail(data) || `${fallback} (status ${res.status}).`);
  }
  return res.json() as Promise<T>;
}

// ---- public interface (kept stable for the panel) -------------------------

export interface HistoryApi {
  listVersions(resumeId: string): Promise<ResumeVersion[]>;
  restoreOriginal(resumeId: string): Promise<void>;
  undoLastAi(resumeId: string): Promise<void>;
  createSnapshot(resumeId: string, label?: string): Promise<void>;
  restoreVersion(resumeId: string, versionId: string, expectedUpdatedAt?: string): Promise<void>;
  compareVersions(resumeId: string, a: string, b: string): Promise<VersionCompare>;
  getVersion(resumeId: string, versionId: string): Promise<VersionWithData>;
}

/** List snapshot metadata (newest first). Empty on a disabled/absent surface. */
export async function listVersions(resumeId: string, cursor?: string): Promise<ResumeVersion[]> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  const res = await apiFetch(`/resumes/${resumeId}/versions${qs}`, { credentials: 'include' });
  // Feature flag off / no resume -> treat as no history rather than an error.
  if (res.status === 404) return [];
  const body = await asJson<RawVersionList>(res, 'Failed to load version history');
  return body.items.map(mapVersion);
}

/** Fetch a single snapshot's metadata + decompressed data on demand (R3.1). */
export async function getVersion(resumeId: string, versionId: string): Promise<VersionWithData> {
  const res = await apiFetch(`/resumes/${resumeId}/versions/${versionId}`, {
    credentials: 'include',
  });
  const r = await asJson<RawVersion & { processed_data: Record<string, unknown> }>(
    res,
    'Failed to load version'
  );
  return { ...mapVersion(r), sizeBytes: r.size_bytes, processedData: r.processed_data };
}

/** Capture the current resume state as a labeled manual snapshot. */
export async function createSnapshot(resumeId: string, label?: string): Promise<void> {
  const res = await apiPost(`/resumes/${resumeId}/versions`, { label: label ?? null });
  await asJson<unknown>(res, 'Failed to save version');
}

/** Restore a specific snapshot (non-destructive; snapshots current first). */
export async function restoreVersion(
  resumeId: string,
  versionId: string,
  expectedUpdatedAt?: string
): Promise<void> {
  const res = await apiPost(`/resumes/${resumeId}/versions/${versionId}/restore`, {
    expected_updated_at: expectedUpdatedAt ?? null,
  });
  await asJson<unknown>(res, 'Failed to restore version');
}

/** Restore the retained `original` snapshot (R2.2). */
export async function restoreOriginal(resumeId: string): Promise<void> {
  // The panel offers "restore original" without a version id; resolve it from
  // the metadata list (the original is always retained).
  const versions = await listVersions(resumeId);
  const original = versions.find((v) => v.source === 'original');
  if (!original) {
    throw new Error('No original snapshot is available to restore.');
  }
  await restoreVersion(resumeId, original.id);
}

/** Restore the snapshot immediately preceding the last AI change (R2.2). */
export async function undoLastAi(resumeId: string): Promise<void> {
  const res = await apiPost(`/resumes/${resumeId}/undo-last-ai`, {});
  await asJson<unknown>(res, 'Failed to undo the last AI change');
}

/** Field-level diff between two owned snapshots (R3.2). */
export async function compareVersions(
  resumeId: string,
  a: string,
  b: string
): Promise<VersionCompare> {
  const res = await apiFetch(
    `/resumes/${resumeId}/versions/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
    { credentials: 'include' }
  );
  const raw = await asJson<{ a: RawVersion; b: RawVersion; changes: VersionDiffEntry[] }>(
    res,
    'Failed to compare versions'
  );
  return { a: mapVersion(raw.a), b: mapVersion(raw.b), changes: raw.changes };
}

export const historyApi: HistoryApi = {
  listVersions,
  restoreOriginal,
  undoLastAi,
  createSnapshot,
  restoreVersion,
  compareVersions,
  getVersion,
};
