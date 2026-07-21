'use client';

/**
 * <SampleGallery> - browse the Resume Sample Library.
 *
 * Search + category filter over {@link RESUME_SAMPLES}; each card is a real
 * render of the sample (page 1) via the shared {@link ResumeDocument} in the
 * sample's recommended template, lazily mounted. Clicking a card opens its
 * detail page; "Use this sample" is on the detail page.
 */
import * as React from 'react';
import Link from 'next/link';
import Search from 'lucide-react/dist/esm/icons/search';
import Star from 'lucide-react/dist/esm/icons/star';

import { Input } from '@/components/atelier/input';
import { Badge } from '@/components/atelier/badge';
import { cn } from '@/lib/utils';
import { ResumeDocument } from '@/components/resume/resume-document';
import { type ResumeSample, RESUME_SAMPLES, filterSamples } from '@/lib/resume/sample-catalog';
import {
  type TemplateCategory,
  TEMPLATE_CATEGORIES,
  getTemplateById,
  templateToSettings,
} from '@/lib/resume/template-catalog';

function LazyMount({ children, minHeight }: { children: React.ReactNode; minHeight: number }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [shown, setShown] = React.useState(typeof IntersectionObserver === 'undefined');
  React.useEffect(() => {
    if (shown) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => entries.some((e) => e.isIntersecting) && (setShown(true), io.disconnect()),
      { rootMargin: '200px' }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [shown]);
  return (
    <div ref={ref} style={{ minHeight }}>
      {shown ? children : null}
    </div>
  );
}

function SampleThumb({ sample }: { sample: ResumeSample }) {
  const template = getTemplateById(sample.recommendedTemplateId);
  const settings = React.useMemo(
    () => (template ? templateToSettings(template) : undefined),
    [template]
  );
  return (
    <div
      inert
      aria-hidden
      className="pointer-events-none overflow-hidden rounded-t-[var(--radius-at-lg)] bg-white"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      <ResumeDocument data={sample.data} settings={settings} maxPages={1} />
    </div>
  );
}

export function SampleGallery() {
  const [query, setQuery] = React.useState('');
  const [category, setCategory] = React.useState<TemplateCategory | 'all'>('all');

  const visible = React.useMemo(
    () => filterSamples(RESUME_SAMPLES, { query, category }),
    [query, category]
  );

  return (
    <div className="space-y-5">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search samples - role, industry, keyword..."
          aria-label="Search samples"
          className="pl-9"
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <CategoryChip active={category === 'all'} onClick={() => setCategory('all')}>
          All
        </CategoryChip>
        {TEMPLATE_CATEGORIES.map((c) => (
          <CategoryChip key={c.id} active={category === c.id} onClick={() => setCategory(c.id)}>
            {c.label}
          </CategoryChip>
        ))}
      </div>

      <p className="text-sm text-[var(--muted-foreground)]" role="status">
        {visible.length} sample{visible.length === 1 ? '' : 's'}
      </p>

      {visible.length === 0 ? (
        <p className="py-12 text-center text-sm text-[var(--muted-foreground)]">
          No samples match your search.
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((s) => (
            <li key={s.id}>
              <Link
                href={`/samples/${s.id}`}
                className="group block h-full overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] transition-shadow hover:shadow-[var(--shadow-at-e2)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                aria-label={`View the ${s.name} sample`}
              >
                <LazyMount minHeight={220}>
                  <SampleThumb sample={s} />
                </LazyMount>
                <div className="flex flex-col gap-2 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-semibold leading-tight">{s.name}</h3>
                    <span
                      className="inline-flex items-center gap-0.5"
                      title={`ATS ${s.atsScore}/5`}
                    >
                      {[1, 2, 3, 4, 5].map((i) => (
                        <Star
                          key={i}
                          className="h-3 w-3"
                          fill={i <= s.atsScore ? 'var(--at-warning)' : 'none'}
                          stroke="var(--at-warning)"
                          aria-hidden
                        />
                      ))}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant="outline">{s.industry}</Badge>
                    <Badge variant="neutral">{s.experienceLevel}</Badge>
                  </div>
                  <p className="line-clamp-2 text-xs text-[var(--muted-foreground)]">
                    {s.description}
                  </p>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CategoryChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-3 py-1 text-sm transition-colors',
        active
          ? 'border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]'
          : 'border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--primary)]'
      )}
    >
      {children}
    </button>
  );
}

export default SampleGallery;
