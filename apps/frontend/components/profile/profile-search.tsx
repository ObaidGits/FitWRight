'use client';

/**
 * Profile search — quick, ranked, highlighted lookup across the whole profile.
 *
 * Debounced query to the backend search endpoint; results render with matched
 * terms marked; choosing a result jumps to the owning section. Keyboard- and
 * screen-reader-navigable (combobox + listbox semantics).
 */
import * as React from 'react';
import Search from 'lucide-react/dist/esm/icons/search';

import { Input } from '@/components/atelier/input';
import { searchProfile, type ProfileSearchResult } from '@/lib/api/professional-profile';

/** Render backend ``[[term]]`` highlight sentinels as <mark> elements. */
function Highlighted({ text }: { text: string }) {
  if (!text) return null;
  const parts = text.split(/(\[\[.*?\]\])/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('[[') && p.endsWith(']]') ? (
          <mark key={i} className="rounded bg-[var(--primary)]/20 px-0.5 text-[var(--foreground)]">
            {p.slice(2, -2)}
          </mark>
        ) : (
          <React.Fragment key={i}>{p}</React.Fragment>
        )
      )}
    </>
  );
}

const TYPE_LABEL: Record<string, string> = {
  experience: 'Experience',
  education: 'Education',
  project: 'Project',
  skill: 'Skill',
  certification: 'Certification',
  achievement: 'Achievement',
  summary: 'Summary',
  identity: 'Profile',
};

export function ProfileSearch({ onNavigate }: { onNavigate: (section: string) => void }) {
  const [query, setQuery] = React.useState('');
  const [results, setResults] = React.useState<ProfileSearchResult[]>([]);
  const [open, setOpen] = React.useState(false);
  const listId = 'profile-search-results';

  React.useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const r = await searchProfile(query.trim());
        if (cancelled) return;
        setResults(r);
        setOpen(true);
      } catch {
        /* best-effort */
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query]);

  function choose(r: ProfileSearchResult) {
    onNavigate(r.section);
    setOpen(false);
    setQuery('');
  }

  return (
    <div className="relative w-full max-w-xs">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Search your profile…"
          aria-label="Search your profile"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          className="pl-8"
        />
      </div>
      {open && results.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--popover,var(--card))] py-1 shadow-[var(--shadow-at-e2)]"
        >
          {results.map((r) => (
            <li key={`${r.type}-${r.uid}`} role="option" aria-selected={false}>
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(r);
                }}
                className="flex w-full flex-col items-start px-3 py-2 text-left hover:bg-[var(--accent)]"
              >
                <span className="text-sm text-[var(--foreground)]">
                  <Highlighted text={r.title} />
                </span>
                <span className="text-xs text-[var(--muted-foreground)]">
                  {TYPE_LABEL[r.type] ?? r.type}
                  {r.subtitle && (
                    <>
                      {' · '}
                      <Highlighted text={r.subtitle} />
                    </>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
