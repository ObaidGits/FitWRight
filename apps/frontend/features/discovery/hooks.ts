'use client';

/** React Query hooks for the Job Discovery feature. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  postRecommend,
  getCachedRecommendations,
  postTailor,
  listRecipes,
  createRecipe,
  updateRecipe,
  deleteRecipe,
  type RecommendResponse,
  type SearchFilters,
  type SiteRecipe,
  type SiteRecipeCreate,
  type SiteRecipeUpdate,
  type TailorRequest,
  type TailorResponse,
} from '@/lib/api/discovery';

// Key factory
const keys = {
  all: ['discovery'] as const,
  recommendations: (resumeId?: string | null, filters?: SearchFilters | null) =>
    [...keys.all, 'recommendations', resumeId ?? null, filters ?? null] as const,
  recipes: () => [...keys.all, 'recipes'] as const,
};

// -------------------------------------------------------------------------- //
// Recommendations
// -------------------------------------------------------------------------- //

export interface UseRecommendationsParams {
  resumeId: string | null | undefined;
  filters?: SearchFilters | null;
  forceRefresh?: boolean;
  enabled?: boolean;
}

/** Run discovery (POST /recommend) gated by resumeId + enabled. */
export function useRecommendations(params: UseRecommendationsParams) {
  const { resumeId, filters, forceRefresh = false, enabled = true } = params;
  return useQuery<RecommendResponse, Error>({
    queryKey: [...keys.recommendations(resumeId, filters), forceRefresh],
    queryFn: ({ signal }) =>
      postRecommend(
        { resume_id: resumeId as string, filters: filters ?? null, force_refresh: forceRefresh },
        signal
      ),
    enabled: enabled && Boolean(resumeId),
  });
}

/** Read last cached recommendations (GET) without running a new fan-out. */
export function useCachedRecommendations(
  resumeId: string | null | undefined,
  filters?: SearchFilters | null,
  options?: { enabled?: boolean }
) {
  return useQuery<RecommendResponse, Error>({
    queryKey: [...keys.recommendations(resumeId, filters), 'cached'],
    queryFn: ({ signal }) => getCachedRecommendations(resumeId as string, filters, signal),
    enabled: (options?.enabled ?? true) && Boolean(resumeId),
  });
}

// -------------------------------------------------------------------------- //
// Tailor handoff
// -------------------------------------------------------------------------- //

/** Hand a listing to the tailor flow, returns {job_id, resume_id}. */
export function useTailorForJob() {
  return useMutation<TailorResponse, Error, TailorRequest>({
    mutationFn: (request) => postTailor(request),
  });
}

// -------------------------------------------------------------------------- //
// Site recipes
// -------------------------------------------------------------------------- //

export function useSiteRecipes(options?: { enabled?: boolean }) {
  return useQuery<SiteRecipe[], Error>({
    queryKey: keys.recipes(),
    queryFn: ({ signal }) => listRecipes(signal),
    enabled: options?.enabled ?? true,
  });
}

export function useCreateSiteRecipe() {
  const qc = useQueryClient();
  return useMutation<SiteRecipe, Error, SiteRecipeCreate>({
    mutationFn: (body) => createRecipe(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.recipes() }),
  });
}

export function useUpdateSiteRecipe() {
  const qc = useQueryClient();
  return useMutation<SiteRecipe, Error, { slug: string; body: SiteRecipeUpdate }>({
    mutationFn: ({ slug, body }) => updateRecipe(slug, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.recipes() }),
  });
}

export function useDeleteSiteRecipe() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (slug) => deleteRecipe(slug),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.recipes() }),
  });
}


// -------------------------------------------------------------------------- //
// Feed (Phase 1 — background discovery)
// -------------------------------------------------------------------------- //

import {
  getFeed,
  getUnseenCount,
  enableSchedule,
  toggleSchedule,
  type FeedResponse,
  type FeedParams,
} from '@/lib/api/discovery';

const feedKeys = {
  feed: (status?: string) => ['discovery', 'feed', status ?? 'all'] as const,
  unseen: () => ['discovery', 'unseen'] as const,
};

/** Paginated job feed — jobs that background discovery found for you. */
export function useDiscoveryFeed(params?: FeedParams) {
  return useQuery<FeedResponse, Error>({
    // Every filter belongs in the key: two different filter sets are two
    // different result sets, and sharing a cache entry between them is what
    // makes a list keep showing rows the user just filtered out.
    queryKey: [
      ...feedKeys.feed(params?.status),
      params?.sources?.join(',') ?? '',
      params?.q ?? '',
      params?.location ?? '',
      params?.isRemote ?? false,
      params?.minScore ?? 0,
      params?.postedWithinHours ?? 0,
      params?.limit,
      params?.offset,
    ],
    queryFn: ({ signal }) => getFeed(params, signal),
  });
}

/** Unseen count for the nav badge. */
export function useUnseenCount() {
  return useQuery<{ unseen: number }, Error>({
    queryKey: feedKeys.unseen(),
    queryFn: ({ signal }) => getUnseenCount(signal),
    refetchInterval: 60_000, // poll every minute for badge updates
  });
}

/** Enable background discovery for a resume. */
export function useEnableSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ resumeId, intervalHours }: { resumeId: string; intervalHours?: number }) =>
      enableSchedule(resumeId, intervalHours),
    onSuccess: () => void qc.invalidateQueries({ queryKey: feedKeys.feed() }),
  });
}

/** Toggle background discovery on/off. */
export function useToggleSchedule() {
  return useMutation({
    mutationFn: ({ resumeId, enabled }: { resumeId: string; enabled: boolean }) =>
      toggleSchedule(resumeId, enabled),
  });
}


// -------------------------------------------------------------------------- //
// Manual search (no resume required)
// -------------------------------------------------------------------------- //

import {
  manualSearch,
  type ManualSearchRequest,
  type ManualSearchResponse,
} from '@/lib/api/discovery';

/** Manual job search — direct query, no resume needed. */
export function useManualSearch() {
  return useMutation<ManualSearchResponse, Error, ManualSearchRequest>({
    mutationFn: (params) => manualSearch(params),
  });
}


// -------------------------------------------------------------------------- //
// Status management + cleanup
// -------------------------------------------------------------------------- //

import { updateResultStatus, cleanupFeed } from '@/lib/api/discovery';

/** Update a feed result's status (interested/dismissed/applied). */
export function useUpdateResultStatus() {
  const qc = useQueryClient();
  return useMutation<
    { id: string; status: string; queued?: boolean },
    Error,
    { id: string; status: string }
  >({
    mutationFn: ({ id, status }) => updateResultStatus(id, status),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['discovery', 'feed'] });
      void qc.invalidateQueries({ queryKey: ['discovery', 'unseen'] });
      // Saving a job now creates an apply-queue entry, so the queue, the tracker
      // board and Home's "Next up" card are all stale until refetched.
      void qc.invalidateQueries({ queryKey: ['applications'] });
    },
  });
}

/** Clean up old feed results (archive new/dismissed older than N days). */
export function useCleanupFeed() {
  const qc = useQueryClient();
  return useMutation<{ deleted: number }, Error, number>({
    mutationFn: (days) => cleanupFeed(days),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['discovery', 'feed'] }),
  });
}
