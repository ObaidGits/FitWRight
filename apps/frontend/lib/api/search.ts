/**
 * Search API (P3 §C, Requirements 7–8) — server FTS with client-side fallback.
 *
 * {@link searchServer} hits the backend FTS index (ranked, scoped, paginated);
 * {@link searchLocal} is the OFFLINE fallback over already-loaded object-graph
 * nodes (used when the network call fails or while offline). {@link recentSearches}
 * keeps the last few queries locally (no PII leaves the device) so the palette
 * can offer them.
 */
export type SearchNodeType = 'resume' | 'application' | 'jd' | 'job';

export interface SearchResult {
  nodeType: SearchNodeType;
  id: string;
  title: string;
  snippet?: string;
  href: string;
}

export interface SearchIndexItem extends SearchResult {
  haystack: string; // lowercased searchable text
}

/** Pure client-side matcher over provided items (no network) — offline fallback. */
export function searchLocal(items: SearchIndexItem[], query: string, limit = 20): SearchResult[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const terms = q.split(/\s+/);
  return items
    .filter((it) => terms.every((t) => it.haystack.includes(t)))
    .slice(0, limit)
    .map(
      (it): SearchResult => ({
        nodeType: it.nodeType,
        id: it.id,
        title: it.title,
        snippet: it.snippet,
        href: it.href,
      })
    );
}

// ---------------------------------------------------------------------------
// Server search
// ---------------------------------------------------------------------------

import { apiFetch, apiPost } from './client';

interface RawSearchResult {
  node_type: 'resume' | 'job' | 'application';
  node_id: string;
  title: string;
  status: string | null;
  updated_at: string;
  rank: number;
}

interface RawSearchResponse {
  items: RawSearchResult[];
  next_cursor: string | null;
  query: string;
}

/** Deep-link path for a node (matches the app routes). */
function hrefFor(nodeType: string, id: string): string {
  if (nodeType === 'resume') return `/resumes/${id}`;
  if (nodeType === 'application') return `/applications/${id}`;
  return '/applications';
}

export interface ServerSearchPage {
  results: SearchResult[];
  nextCursor: string | null;
}

/** Query the backend FTS index. Returns [] on 404 (feature off). */
export async function searchServer(
  query: string,
  opts?: { types?: SearchNodeType[]; limit?: number; cursor?: string; signal?: AbortSignal }
): Promise<ServerSearchPage> {
  const q = query.trim();
  if (!q) return { results: [], nextCursor: null };
  const params = new URLSearchParams({ q });
  if (opts?.types?.length) params.set('types', opts.types.join(','));
  if (opts?.limit) params.set('limit', String(opts.limit));
  if (opts?.cursor) params.set('cursor', opts.cursor);
  const res = await apiFetch(`/search?${params.toString()}`, {
    credentials: 'include',
    signal: opts?.signal,
  });
  if (res.status === 404) return { results: [], nextCursor: null };
  if (!res.ok) throw new Error(`Search failed (status ${res.status}).`);
  const body = (await res.json()) as RawSearchResponse;
  return {
    results: body.items.map((r) => ({
      nodeType: r.node_type,
      id: r.node_id,
      title: r.title,
      snippet: r.status ?? undefined,
      href: hrefFor(r.node_type, r.node_id),
    })),
    nextCursor: body.next_cursor,
  };
}

/** Rebuild the caller's server-side index (recovery / initial backfill). */
export async function reindexSearch(): Promise<void> {
  await apiPost('/search/reindex', {});
}

// ---------------------------------------------------------------------------
// Recent searches (local only)
// ---------------------------------------------------------------------------

const RECENT_KEY = 'fitwright.recentSearches';
const RECENT_MAX = 6;

export function recentSearches(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]).slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function addRecentSearch(query: string): void {
  if (typeof window === 'undefined') return;
  const q = query.trim();
  if (!q) return;
  try {
    const next = [q, ...recentSearches().filter((x) => x.toLowerCase() !== q.toLowerCase())].slice(
      0,
      RECENT_MAX
    );
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota/serialization errors */
  }
}
