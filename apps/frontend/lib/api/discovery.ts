/**
 * Job Discovery API (§10.5, design §9).
 *
 * One function per backend endpoint under `/api/v1/discovery`.
 * Follows the same raw-Response pattern as other `lib/api/` modules.
 */

import { apiFetch } from './client';

// -------------------------------------------------------------------------- //
// Types (mirroring apps/backend/app/schemas/discovery.py)
// -------------------------------------------------------------------------- //

export type FetchMode = 'http' | 'stealth';

export interface SearchFilters {
  location?: string | null;
  is_remote?: boolean | null;
  hours_old?: number | null;
  results_wanted?: number | null;
  country_indeed?: string | null;
}

export interface JobListing {
  source: string;
  title: string;
  company: string;
  location: string;
  url: string;
  is_remote?: boolean | null;
  description?: string | null;
  posted_at?: string | null;
  salary?: string | null;
  fingerprint: string;
}

export interface Recommendation {
  listing: JobListing;
  match_score: number;
  partial: boolean;
  matched: string[];
  missing: string[];
}

export interface SourceFailure {
  source: string;
  reason: string;
  kind?: string | null;
}

export interface SearchQuery {
  titles: string[];
  search_string: string;
  seniority?: string | null;
  location?: string | null;
  country_indeed?: string | null;
  degraded: boolean;
}

export interface RecommendRequest {
  resume_id: string;
  filters?: SearchFilters | null;
  force_refresh?: boolean;
}

export interface RecommendResponse {
  recommendations: Recommendation[];
  query: SearchQuery | null;
  degraded: boolean;
  cached: boolean;
  failures: SourceFailure[];
}

export interface TailorRequest {
  resume_id: string;
  listing: JobListing;
}

export interface TailorResponse {
  job_id: string;
  resume_id: string;
}

export interface SiteRecipe {
  id?: number | null;
  user_id: string;
  name: string;
  slug: string;
  base_url: string;
  search_url_template: string;
  schema: Record<string, unknown>;
  fetch_mode: FetchMode;
  enabled: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SiteRecipeCreate {
  name: string;
  slug: string;
  base_url: string;
  search_url_template: string;
  schema?: Record<string, unknown>;
  fetch_mode?: FetchMode;
  enabled?: boolean;
}

export interface SiteRecipeUpdate {
  name?: string;
  base_url?: string;
  search_url_template?: string;
  schema?: Record<string, unknown>;
  fetch_mode?: FetchMode;
  enabled?: boolean;
}

// -------------------------------------------------------------------------- //
// API functions
// -------------------------------------------------------------------------- //

const PREFIX = '/api/v1/discovery';

/** POST /discovery/recommend — run discovery and return ranked recommendations. */
export async function postRecommend(
  body: RecommendRequest,
  signal?: AbortSignal
): Promise<RecommendResponse> {
  const res = await apiFetch(`${PREFIX}/recommend`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`Discovery recommend failed: ${res.status}`);
  return res.json();
}

/** GET /discovery/recommend/{resume_id} — last cached recommendations. */
export async function getCachedRecommendations(
  resumeId: string,
  filters?: SearchFilters | null,
  signal?: AbortSignal
): Promise<RecommendResponse> {
  const params = new URLSearchParams();
  if (filters?.location) params.set('location', filters.location);
  if (filters?.is_remote != null) params.set('is_remote', String(filters.is_remote));
  if (filters?.hours_old != null) params.set('hours_old', String(filters.hours_old));
  if (filters?.results_wanted != null) params.set('results_wanted', String(filters.results_wanted));
  if (filters?.country_indeed) params.set('country_indeed', filters.country_indeed);
  const qs = params.toString();
  const url = `${PREFIX}/recommend/${encodeURIComponent(resumeId)}${qs ? `?${qs}` : ''}`;
  const res = await apiFetch(url, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Cached recommendations failed: ${res.status}`);
  return res.json();
}

/** POST /discovery/tailor — hand a listing to the tailor flow. */
export async function postTailor(body: TailorRequest): Promise<TailorResponse> {
  const res = await apiFetch(`${PREFIX}/tailor`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Tailor handoff failed: ${res.status}`);
  return res.json();
}

/** GET /discovery/recipes — list the user's site recipes. */
export async function listRecipes(signal?: AbortSignal): Promise<SiteRecipe[]> {
  const res = await apiFetch(`${PREFIX}/recipes`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`List recipes failed: ${res.status}`);
  return res.json();
}

/** POST /discovery/recipes — create a new recipe. */
export async function createRecipe(body: SiteRecipeCreate): Promise<SiteRecipe> {
  const res = await apiFetch(`${PREFIX}/recipes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Create recipe failed: ${res.status}`);
  return res.json();
}

/** PUT /discovery/recipes/{slug} — update a recipe. */
export async function updateRecipe(slug: string, body: SiteRecipeUpdate): Promise<SiteRecipe> {
  const res = await apiFetch(`${PREFIX}/recipes/${encodeURIComponent(slug)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Update recipe failed: ${res.status}`);
  return res.json();
}

/** DELETE /discovery/recipes/{slug} — delete a recipe. */
export async function deleteRecipe(slug: string): Promise<void> {
  const res = await apiFetch(`${PREFIX}/recipes/${encodeURIComponent(slug)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`Delete recipe failed: ${res.status}`);
}


// -------------------------------------------------------------------------- //
// Feed endpoints (Phase 1 — background discovery)
// -------------------------------------------------------------------------- //

export interface FeedResult {
  id: string;
  fingerprint: string;
  source: string;
  title: string;
  company: string;
  location: string;
  url: string;
  is_remote?: boolean | null;
  description?: string | null;
  salary?: string | null;
  posted_at?: string | null;
  match_score: number;
  matched_keywords: string[];
  missing_keywords: string[];
  partial: boolean;
  status: string;
  seen: boolean;
  created_at: string;
  /** The job-description row created when this was saved, if it has been. */
  job_id?: string | null;
  /**
   * Other boards carrying this same job. Present only when duplicates were
   * collapsed, so its absence means "seen once", not "unknown".
   */
  also_on?: string[];
  /** How many board listings collapsed into this row, including this one. */
  duplicate_count?: number;
}

export interface FeedResponse {
  results: FeedResult[];
  total: number;
  /**
   * Rows on this page after same-job duplicates collapsed. Lower than the page
   * size when boards overlapped - reported separately so the page can explain
   * the difference instead of looking like it lost jobs.
   */
  shown?: number;
  unseen: number;
  /**
   * How many jobs in the whole feed carry a real match score. Zero means nothing
   * has been matched against a resume yet, so a score filter could only ever
   * return nothing - the UI hides the control instead of offering a dead end.
   */
  scored: number;
  limit: number;
  offset: number;
}

export interface ScheduleResponse {
  schedule: Record<string, unknown>;
  message: string;
}

/** GET /discovery/feed — paginated job feed. */
/** Filters accepted by the feed. All optional, combined with AND server-side. */
export interface FeedParams {
  status?: string;
  /** Board ids to include. Omit or leave empty for every board. */
  sources?: string[];
  /** Every token must appear in the title or company. */
  q?: string;
  location?: string;
  isRemote?: boolean;
  /** Match percentage floor, 0-100 as the UI shows it. */
  minScore?: number;
  /** Recency window in hours. Jobs with no published date use when we found them. */
  postedWithinHours?: number;
  limit?: number;
  offset?: number;
}

export async function getFeed(
  params?: FeedParams,
  signal?: AbortSignal,
): Promise<FeedResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.sources?.length) qs.set('sources', params.sources.join(','));
  if (params?.q?.trim()) qs.set('q', params.q.trim());
  if (params?.location?.trim()) qs.set('location', params.location.trim());
  if (params?.isRemote) qs.set('is_remote', 'true');
  if (params?.minScore) qs.set('min_score', String(params.minScore));
  if (params?.postedWithinHours) qs.set('posted_within_hours', String(params.postedWithinHours));
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.offset) qs.set('offset', String(params.offset));
  const query = qs.toString();
  const res = await apiFetch(`${PREFIX}/feed${query ? `?${query}` : ''}`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Feed failed: ${res.status}`);
  return res.json();
}

/** GET /discovery/feed/unseen — badge count. */
export async function getUnseenCount(signal?: AbortSignal): Promise<{ unseen: number }> {
  const res = await apiFetch(`${PREFIX}/feed/unseen`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Unseen count failed: ${res.status}`);
  return res.json();
}

/** POST /discovery/feed/schedule — enable background discovery. */
export async function enableSchedule(
  resumeId: string,
  intervalHours: number = 24,
): Promise<ScheduleResponse> {
  const res = await apiFetch(`${PREFIX}/feed/schedule?resume_id=${encodeURIComponent(resumeId)}&interval_hours=${intervalHours}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Enable schedule failed: ${res.status}`);
  return res.json();
}

/** POST /discovery/feed/schedule/toggle — pause/resume background discovery. */
export async function toggleSchedule(
  resumeId: string,
  enabled: boolean,
): Promise<{ enabled: boolean; message: string }> {
  const res = await apiFetch(
    `${PREFIX}/feed/schedule/toggle?resume_id=${encodeURIComponent(resumeId)}&enabled=${enabled}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' } },
  );
  if (!res.ok) throw new Error(`Toggle schedule failed: ${res.status}`);
  return res.json();
}


// -------------------------------------------------------------------------- //
// Manual search (no resume required)
// -------------------------------------------------------------------------- //

export interface ManualSearchRequest {
  query: string;
  location?: string | null;
  is_remote?: boolean | null;
  hours_old?: number | null;
  results_wanted?: number | null;
  country_indeed?: string | null;
  sites?: string[] | null;
  job_type?: string | null; // fulltime, parttime, internship, contract
  distance?: number | null; // miles from location
  resume_id?: string | null;
}

export interface ManualSearchResponse {
  results: Array<{
    listing: JobListing;
    match_score: number;
    partial: boolean;
    matched: string[];
    missing: string[];
  }>;
  total: number;
  query: string;
  sites: string[];
  degraded: boolean;
  failures: Array<{ source: string; reason: string }>;
}

/** POST /discovery/search — manual job search (no resume needed). */
export async function manualSearch(
  body: ManualSearchRequest,
  signal?: AbortSignal,
): Promise<ManualSearchResponse> {
  const res = await apiFetch(`${PREFIX}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`Manual search failed: ${res.status}`);
  return res.json();
}


// -------------------------------------------------------------------------- //
// Status + Cleanup + Schedule editing
// -------------------------------------------------------------------------- //

/** PATCH /discovery/feed/{id}/status — update a job's status. */
export async function updateResultStatus(
  resultId: string,
  status: string,
): Promise<{ id: string; status: string; queued?: boolean }> {
  const res = await apiFetch(`${PREFIX}/feed/${encodeURIComponent(resultId)}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(`Update status failed: ${res.status}`);
  return res.json();
}

/** POST /discovery/feed/cleanup — archive old results. */
export async function cleanupFeed(days: number = 30): Promise<{ deleted: number }> {
  const res = await apiFetch(`${PREFIX}/feed/cleanup?days=${days}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Cleanup failed: ${res.status}`);
  return res.json();
}

/** PATCH /discovery/feed/schedule — edit schedule. */
export async function editSchedule(
  resumeId: string,
  updates: { interval_hours?: number; resume_id?: string },
): Promise<{ message: string }> {
  const res = await apiFetch(`${PREFIX}/feed/schedule?resume_id=${encodeURIComponent(resumeId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Edit schedule failed: ${res.status}`);
  return res.json();
}
