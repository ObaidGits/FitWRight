'use client';

/**
 * Skill tag input — chips + autocomplete over the Canonical Skill Engine.
 *
 * Replaces free-text comma lists with a proper token editor: existing skills
 * render as removable chips, typing queries the backend skill autocomplete
 * (debounced), and Enter / click / comma commits a skill. Fully keyboard- and
 * screen-reader-navigable. The parent owns the string list; canonicalization
 * happens server-side on save.
 */
import * as React from 'react';
import X from 'lucide-react/dist/esm/icons/x';

import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { suggestSkills, type SkillSuggestion } from '@/lib/api/professional-profile';

export function SkillTagInput({
  id,
  label,
  values,
  onChange,
  placeholder,
  autocomplete = false,
}: {
  id: string;
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  autocomplete?: boolean;
}) {
  const [draft, setDraft] = React.useState('');
  const [suggestions, setSuggestions] = React.useState<SkillSuggestion[]>([]);
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(-1);
  const listId = `${id}-suggestions`;

  const lower = React.useMemo(() => new Set(values.map((v) => v.toLowerCase())), [values]);

  // Debounced autocomplete lookup.
  React.useEffect(() => {
    if (!autocomplete || draft.trim().length < 1) {
      setSuggestions([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const results = await suggestSkills(draft.trim());
        if (cancelled) return;
        const filtered = results.filter((r) => !lower.has(r.displayName.toLowerCase()));
        setSuggestions(filtered);
        setOpen(filtered.length > 0);
        setActive(-1);
      } catch {
        /* autocomplete is best-effort; typing still works */
      }
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [draft, autocomplete, lower]);

  function add(name: string) {
    const clean = name.trim();
    if (!clean || lower.has(clean.toLowerCase())) {
      setDraft('');
      setOpen(false);
      return;
    }
    onChange([...values, clean]);
    setDraft('');
    setSuggestions([]);
    setOpen(false);
    setActive(-1);
  }

  function remove(idx: number) {
    onChange(values.filter((_, i) => i !== idx));
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      if (open && active >= 0 && suggestions[active]) add(suggestions[active].displayName);
      else add(draft);
    } else if (e.key === 'Backspace' && !draft && values.length) {
      remove(values.length - 1);
    } else if (open && e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, suggestions.length - 1));
    } else if (open && e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex flex-wrap gap-1.5 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--background)] p-2">
        {values.map((v, idx) => (
          <span
            key={`${v}-${idx}`}
            className="inline-flex items-center gap-1 rounded-full bg-[var(--secondary)] px-2.5 py-0.5 text-xs font-medium text-[var(--secondary-foreground)]"
          >
            {v}
            <button
              type="button"
              onClick={() => remove(idx)}
              aria-label={`Remove ${v}`}
              className="rounded-full p-0.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <div className="relative min-w-[8rem] flex-1">
          <Input
            id={id}
            value={draft}
            placeholder={values.length === 0 ? placeholder : 'Add…'}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={() => setTimeout(() => setOpen(false), 120)}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            className="border-0 bg-transparent px-1 shadow-none focus-visible:ring-0"
          />
          {open && (
            <ul
              id={listId}
              role="listbox"
              className="absolute z-10 mt-1 max-h-48 w-full overflow-y-auto rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--popover,var(--card))] py-1 shadow-[var(--shadow-at-e2)]"
            >
              {suggestions.map((s, i) => (
                <li key={s.canonical} role="option" aria-selected={i === active}>
                  <button
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      add(s.displayName);
                    }}
                    className={
                      'flex w-full items-center px-3 py-1.5 text-left text-sm ' +
                      (i === active
                        ? 'bg-[var(--accent)] text-[var(--foreground)]'
                        : 'text-[var(--foreground)] hover:bg-[var(--accent)]')
                    }
                  >
                    {s.displayName}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
