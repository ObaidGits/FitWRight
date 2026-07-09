/**
 * Search interface (Req 32, Task 3.8/20).
 * Client-side search over already-loaded object-graph nodes now; server-side
 * search + recent/favorites/pinned/saved are FUTURE BACKEND behind this
 * same interface.
 */
export type SearchNodeType = 'resume' | 'application' | 'jd';

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

/** Pure client-side matcher over provided items (no network). */
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
