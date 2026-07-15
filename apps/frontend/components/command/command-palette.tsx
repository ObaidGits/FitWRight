'use client';

/**
 * Command palette (⌘K) — Task 3.7 / Req 3.6, 27.3.
 * A POWER-USER ENHANCEMENT, not a required interaction model — every action it
 * offers is also reachable in the visible UI. Combines navigation, object
 * actions, AI commands, global search, and recently-used destinations.
 *
 * Fully keyboard-driven (Linear/Raycast-class):
 * - ↑/↓ move the active option, Home/End jump, Enter runs it, Esc closes.
 * - Roving `aria-activedescendant` + per-option `aria-selected` so screen
 *   readers announce the highlighted command; the active row auto-scrolls in.
 * - Pointer hover and keyboard share one `activeIndex` (no dual highlight).
 * - Recently-used commands/resumes/applications persist and lead the list when
 *   the query is empty (recognition over recall).
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import Search from 'lucide-react/dist/esm/icons/search';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Briefcase from 'lucide-react/dist/esm/icons/briefcase';
import Clock from 'lucide-react/dist/esm/icons/clock';
import CornerDownLeft from 'lucide-react/dist/esm/icons/corner-down-left';
import { Dialog, DialogContent } from '@/components/atelier/dialog';
import { cn } from '@/lib/utils';
import { fetchResumeList } from '@/lib/api/resume';
import { listApplications } from '@/lib/api/tracker';
import {
  searchLocal,
  searchServer,
  addRecentSearch,
  type SearchIndexItem,
  type SearchResult,
} from '@/lib/api/search';

export interface Command {
  id: string;
  label: string;
  group?: string;
  keywords?: string;
  icon?: React.ComponentType<{ className?: string }>;
  /** Destination href, when the command is a navigation (enables recents). */
  href?: string;
  run: () => void;
}

interface CommandPaletteContextValue {
  open: () => void;
  close: () => void;
  register: (commands: Command[]) => () => void;
}

const CommandPaletteContext = React.createContext<CommandPaletteContextValue | undefined>(
  undefined
);

const NAV_COMMANDS: Omit<Command, 'run'>[] = [
  { id: 'nav-home', label: 'Go to Home', group: 'Navigation', keywords: 'home dashboard' },
  { id: 'nav-resumes', label: 'Go to Resumes', group: 'Navigation', keywords: 'resume library' },
  {
    id: 'nav-applications',
    label: 'Go to Applications',
    group: 'Navigation',
    keywords: 'pipeline tracker',
  },
  { id: 'nav-settings', label: 'Go to Settings', group: 'Navigation', keywords: 'settings config' },
  {
    id: 'action-tailor',
    label: 'Tailor to a job',
    group: 'Actions',
    keywords: 'new tailor generate',
    icon: Sparkles,
  },
  {
    id: 'ai-tailor',
    label: 'Tailor a resume with AI',
    group: 'AI',
    keywords: 'ai tailor generate improve job',
    icon: Sparkles,
  },
  {
    id: 'ai-import',
    label: 'Import a resume to enhance with AI',
    group: 'AI',
    keywords: 'ai import upload enhance',
    icon: Sparkles,
  },
];

const NAV_HREF: Record<string, string> = {
  'nav-home': '/home',
  'nav-resumes': '/resumes',
  'nav-applications': '/applications',
  'nav-settings': '/settings',
  'action-tailor': '/tailor',
  'ai-tailor': '/tailor',
  'ai-import': '/import',
};

// ---------------------------------------------------------------------------
// Recently-used commands / destinations (recognition over recall).
// ---------------------------------------------------------------------------
const RECENTS_KEY = 'fitwright-command-recents';
const RECENTS_MAX = 6;

interface RecentEntry {
  id: string;
  label: string;
  href: string;
  /** Search node type when applicable ('resume' | 'application' | 'jd' | …). */
  nodeType?: string;
}

function loadRecents(): RecentEntry[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    const parsed = raw ? (JSON.parse(raw) as RecentEntry[]) : [];
    return Array.isArray(parsed)
      ? parsed.filter((e) => e && e.id && e.href).slice(0, RECENTS_MAX)
      : [];
  } catch {
    return [];
  }
}

function saveRecent(entry: RecentEntry): RecentEntry[] {
  const next = [entry, ...loadRecents().filter((e) => e.id !== entry.id)].slice(0, RECENTS_MAX);
  try {
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable — recents are best-effort */
  }
  return next;
}

export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [isOpen, setIsOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [extra, setExtra] = React.useState<Command[]>([]);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [recents, setRecents] = React.useState<RecentEntry[]>([]);
  const listRef = React.useRef<HTMLUListElement>(null);

  const [debouncedQuery, setDebouncedQuery] = React.useState('');

  const open = React.useCallback(() => setIsOpen(true), []);
  const close = React.useCallback(() => setIsOpen(false), []);

  // Load recents when the palette opens (and reset transient state).
  React.useEffect(() => {
    if (isOpen) {
      setRecents(loadRecents());
      setQuery('');
      setActiveIndex(0);
    }
  }, [isOpen]);

  // Debounce the query feeding the server search (keyboard-first, low-chatter).
  React.useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 180);
    return () => clearTimeout(t);
  }, [query]);

  const register = React.useCallback((commands: Command[]) => {
    setExtra((prev) => [...prev, ...commands]);
    return () => setExtra((prev) => prev.filter((c) => !commands.some((n) => n.id === c.id)));
  }, []);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsOpen((v) => !v);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Record a chosen destination as "recent" (best-effort, navigation only).
  const recordRecent = React.useCallback((entry: RecentEntry | null) => {
    if (!entry) return;
    setRecents(saveRecent(entry));
  }, []);

  const navCommands = React.useMemo<Command[]>(
    () =>
      NAV_COMMANDS.map((c) => ({
        ...c,
        href: NAV_HREF[c.id],
        run: () => {
          recordRecent({ id: c.id, label: c.label, href: NAV_HREF[c.id] });
          router.push(NAV_HREF[c.id]);
          setIsOpen(false);
        },
      })),
    [router, recordRecent]
  );

  // Global search index (Task 20 / Req 32) — fetched lazily only while the
  // palette is open, so it never fires on marketing/auth routes at rest.
  const { data: searchIndex } = useQuery<SearchIndexItem[]>({
    queryKey: ['command-search-index'],
    enabled: isOpen,
    staleTime: 30_000,
    queryFn: async () => {
      const [resumes, apps] = await Promise.all([
        fetchResumeList(true).catch(() => []),
        listApplications().catch(() => ({ columns: {} as Record<string, unknown[]> })),
      ]);
      const items: SearchIndexItem[] = [];
      for (const r of resumes) {
        const title = r.title || r.filename || 'Untitled resume';
        items.push({
          nodeType: 'resume',
          id: r.resume_id,
          title,
          snippet: r.is_master ? 'Master resume' : 'Tailored resume',
          href: `/resumes/${r.resume_id}`,
          haystack: `${title} ${r.jobSnippet ?? ''}`.toLowerCase(),
        });
      }
      const columns =
        (apps as { columns: Record<string, Array<Record<string, unknown>>> }).columns ?? {};
      for (const list of Object.values(columns)) {
        for (const a of list as Array<Record<string, unknown>>) {
          const company = (a.company as string) || '';
          const role = (a.role as string) || '';
          const title = [role, company].filter(Boolean).join(' · ') || 'Application';
          items.push({
            nodeType: 'application',
            id: a.application_id as string,
            title,
            snippet: 'Application',
            href: `/applications/${a.application_id as string}`,
            haystack: `${title}`.toLowerCase(),
          });
        }
      }
      return items;
    },
  });

  // Server-side FTS search (ranked, scoped, cross-node). Falls back to the
  // client-side index on any failure / offline, so search always works.
  const { data: serverResults } = useQuery<SearchResult[]>({
    queryKey: ['command-server-search', debouncedQuery],
    enabled: isOpen && debouncedQuery.trim().length > 0,
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      try {
        const page = await searchServer(debouncedQuery, { limit: 8, signal });
        if (page.results.length > 0) return page.results;
      } catch {
        /* fall through to the offline client index */
      }
      return searchIndex ? searchLocal(searchIndex, debouncedQuery, 8) : [];
    },
  });

  const searchCommands = React.useMemo<Command[]>(() => {
    if (!query.trim()) return [];
    const results: SearchResult[] =
      serverResults ?? (searchIndex ? searchLocal(searchIndex, query, 8) : []);
    return results.map((r) => ({
      id: `search-${r.nodeType}-${r.id}`,
      label: r.title,
      group: r.nodeType === 'resume' ? 'Resumes' : 'Applications',
      keywords: r.snippet,
      icon: r.nodeType === 'resume' ? FileText : Briefcase,
      href: r.href,
      run: () => {
        addRecentSearch(query);
        recordRecent({
          id: `${r.nodeType}-${r.id}`,
          label: r.title,
          href: r.href,
          nodeType: r.nodeType,
        });
        router.push(r.href);
        setIsOpen(false);
      },
    }));
  }, [query, serverResults, searchIndex, router, recordRecent]);

  // Recent destinations (shown only when the query is empty).
  const recentCommands = React.useMemo<Command[]>(() => {
    return recents.map((e) => ({
      id: `recent-${e.id}`,
      label: e.label,
      group: 'Recent',
      icon: e.nodeType === 'application' ? Briefcase : e.nodeType === 'resume' ? FileText : Clock,
      href: e.href,
      run: () => {
        router.push(e.href);
        setIsOpen(false);
      },
    }));
  }, [recents, router]);

  const all = React.useMemo(
    () => [...navCommands, ...extra, ...searchCommands],
    [navCommands, extra, searchCommands]
  );

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      // Empty query: recents first (deduped against the command they point to),
      // then the default nav/action/AI commands.
      const recentHrefs = new Set(recentCommands.map((c) => c.href));
      const base = all.filter((c) => !c.id.startsWith('search-') && !recentHrefs.has(c.href));
      return [...recentCommands, ...base];
    }
    return all.filter(
      (c) =>
        c.id.startsWith('search-') ||
        c.label.toLowerCase().includes(q) ||
        (c.keywords ?? '').toLowerCase().includes(q)
    );
  }, [all, query, recentCommands]);

  // Keep the active index in range whenever the visible list changes.
  React.useEffect(() => {
    setActiveIndex((i) => (filtered.length === 0 ? 0 : Math.min(i, filtered.length - 1)));
  }, [filtered.length]);

  // Scroll the active option into view as it changes.
  React.useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    // Guard: scrollIntoView is unavailable in some environments (jsdom, older
    // engines) — it's a progressive enhancement, never a correctness dependency.
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (filtered.length === 0) return;
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % filtered.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length);
        break;
      case 'Home':
        e.preventDefault();
        setActiveIndex(0);
        break;
      case 'End':
        e.preventDefault();
        setActiveIndex(filtered.length - 1);
        break;
      case 'Enter': {
        e.preventDefault();
        const cmd = filtered[activeIndex];
        if (cmd) cmd.run();
        break;
      }
      default:
        break;
    }
  }

  const value = React.useMemo(() => ({ open, close, register }), [open, close, register]);
  const activeId = filtered[activeIndex] ? `cmd-opt-${filtered[activeIndex].id}` : undefined;

  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent showClose={false} className="max-w-xl p-0">
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-4">
            <Search className="h-4 w-4 text-[var(--muted-foreground)]" />
            <input
              autoFocus
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={onInputKeyDown}
              placeholder="Type a command or search…"
              aria-label="Command palette"
              aria-expanded
              aria-controls="command-palette-listbox"
              aria-activedescendant={activeId}
              role="combobox"
              autoComplete="off"
              spellCheck={false}
              className="h-12 flex-1 bg-transparent text-sm text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]"
            />
            <kbd className="rounded bg-[var(--secondary)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
              ESC
            </kbd>
          </div>
          <ul
            ref={listRef}
            id="command-palette-listbox"
            className="max-h-80 overflow-y-auto p-2"
            role="listbox"
            aria-label="Commands and results"
          >
            {filtered.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-[var(--muted-foreground)]">
                No results
              </li>
            )}
            {filtered.map((c, i) => {
              const active = i === activeIndex;
              return (
                <li key={c.id}>
                  <button
                    id={`cmd-opt-${c.id}`}
                    data-index={i}
                    role="option"
                    aria-selected={active}
                    tabIndex={-1}
                    onClick={() => c.run()}
                    onMouseMove={() => setActiveIndex(i)}
                    className={cn(
                      'flex w-full items-center justify-between gap-3 rounded-[var(--radius-at-md)] px-3 py-2 text-left text-sm',
                      active ? 'bg-[var(--accent)]' : 'hover:bg-[var(--accent)]/60'
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      {c.icon && (
                        <c.icon className="h-4 w-4 shrink-0 text-[var(--muted-foreground)]" />
                      )}
                      <span className="truncate text-[var(--foreground)]">{c.label}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {c.group && (
                        <span className="text-xs text-[var(--muted-foreground)]">{c.group}</span>
                      )}
                      {active && (
                        <CornerDownLeft
                          className="h-3.5 w-3.5 text-[var(--muted-foreground)]"
                          aria-hidden
                        />
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center gap-4 border-t border-[var(--border)] px-4 py-2 text-[11px] text-[var(--muted-foreground)]">
            <span className="flex items-center gap-1">
              <kbd className="rounded bg-[var(--secondary)] px-1 py-0.5">↑↓</kbd> navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded bg-[var(--secondary)] px-1 py-0.5">↵</kbd> select
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded bg-[var(--secondary)] px-1 py-0.5">esc</kbd> close
            </span>
          </div>
        </DialogContent>
      </Dialog>
    </CommandPaletteContext.Provider>
  );
}

export function useCommandPalette(): CommandPaletteContextValue {
  const ctx = React.useContext(CommandPaletteContext);
  if (!ctx) throw new Error('useCommandPalette must be used within a CommandPaletteProvider');
  return ctx;
}
