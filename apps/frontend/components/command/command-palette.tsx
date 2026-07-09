'use client';

/**
 * Command palette (⌘K) — Task 3.7 / Req 3.6, 27.3.
 * A POWER-USER ENHANCEMENT, not a required interaction model — every action it
 * offers is also reachable in the visible UI. Combines navigation, object
 * actions, and (registered later) AI commands + global search.
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import Search from 'lucide-react/dist/esm/icons/search';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Briefcase from 'lucide-react/dist/esm/icons/briefcase';
import { Dialog, DialogContent } from '@/components/atelier/dialog';
import { cn } from '@/lib/utils';
import { fetchResumeList } from '@/lib/api/resume';
import { listApplications } from '@/lib/api/tracker';
import { searchLocal, type SearchIndexItem } from '@/lib/api/search';

export interface Command {
  id: string;
  label: string;
  group?: string;
  keywords?: string;
  icon?: React.ComponentType<{ className?: string }>;
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

export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [isOpen, setIsOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [extra, setExtra] = React.useState<Command[]>([]);

  const open = React.useCallback(() => setIsOpen(true), []);
  const close = React.useCallback(() => setIsOpen(false), []);

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

  const navCommands = React.useMemo<Command[]>(
    () =>
      NAV_COMMANDS.map((c) => ({
        ...c,
        run: () => {
          router.push(NAV_HREF[c.id]);
          setIsOpen(false);
        },
      })),
    [router]
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

  const searchCommands = React.useMemo<Command[]>(() => {
    if (!query.trim() || !searchIndex) return [];
    return searchLocal(searchIndex, query, 8).map((r) => ({
      id: `search-${r.nodeType}-${r.id}`,
      label: r.title,
      group: r.nodeType === 'resume' ? 'Resumes' : 'Applications',
      keywords: r.snippet,
      icon: r.nodeType === 'resume' ? FileText : Briefcase,
      run: () => {
        router.push(r.href);
        setIsOpen(false);
      },
    }));
  }, [query, searchIndex, router]);

  const all = React.useMemo(
    () => [...navCommands, ...extra, ...searchCommands],
    [navCommands, extra, searchCommands]
  );
  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return all.filter((c) => !c.id.startsWith('search-'));
    return all.filter(
      (c) =>
        c.id.startsWith('search-') ||
        c.label.toLowerCase().includes(q) ||
        (c.keywords ?? '').toLowerCase().includes(q)
    );
  }, [all, query]);

  const value = React.useMemo(() => ({ open, close, register }), [open, close, register]);

  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent showClose={false} className="max-w-xl p-0">
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-4">
            <Search className="h-4 w-4 text-[var(--muted-foreground)]" />
            {}
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Type a command or search…"
              aria-label="Command palette"
              className="h-12 flex-1 bg-transparent text-sm text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]"
            />
            <kbd className="rounded bg-[var(--secondary)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
              ESC
            </kbd>
          </div>
          <ul className="max-h-80 overflow-y-auto p-2" role="listbox">
            {filtered.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-[var(--muted-foreground)]">
                No results
              </li>
            )}
            {filtered.map((c) => (
              <li key={c.id}>
                <button
                  role="option"
                  aria-selected={false}
                  onClick={() => c.run()}
                  className={cn(
                    'flex w-full items-center justify-between gap-3 rounded-[var(--radius-at-md)] px-3 py-2 text-left text-sm',
                    'hover:bg-[var(--accent)] focus-visible:bg-[var(--accent)] focus-visible:outline-none'
                  )}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {c.icon && (
                      <c.icon className="h-4 w-4 shrink-0 text-[var(--muted-foreground)]" />
                    )}
                    <span className="truncate text-[var(--foreground)]">{c.label}</span>
                  </span>
                  {c.group && (
                    <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                      {c.group}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
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
