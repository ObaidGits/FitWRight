'use client';

/**
 * <TemplateGallery> - the premium, metadata-driven template browser.
 *
 * Renders the {@link RESUME_TEMPLATES} catalog with search, category + photo +
 * ATS filtering, sorting, favorites, and (optionally) personalized "Recommended
 * for you" ranking. Every card thumbnail is a REAL render of the template via
 * the shared {@link ResumeDocument} engine (page 1, lazily mounted), so what the
 * user browses is exactly what they'll get - no separate preview art to drift.
 */
import * as React from 'react';
import Star from 'lucide-react/dist/esm/icons/star';
import Search from 'lucide-react/dist/esm/icons/search';
import Check from 'lucide-react/dist/esm/icons/check';
import ImageIcon from 'lucide-react/dist/esm/icons/image';

import { Button } from '@/components/atelier/button';
import { Badge } from '@/components/atelier/badge';
import { Input } from '@/components/atelier/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/atelier/dialog';
import { cn } from '@/lib/utils';
import type { ResumeData } from '@/components/dashboard/resume-component';
import { ResumeDocument } from '@/components/resume/resume-document';
import { SAMPLE_RESUME, SAMPLE_RESUME_WITH_PHOTO } from '@/lib/resume/sample-resume';
import {
  type ResumeTemplate,
  type TemplateCategory,
  type TemplateSort,
  RESUME_TEMPLATES,
  TEMPLATE_CATEGORIES,
  filterTemplates,
  sortTemplates,
  templateToSettings,
} from '@/lib/resume/template-catalog';
import { type RecommendSignal, recommendTemplates } from '@/lib/resume/template-recommend';

const FAVORITES_KEY = 'fitwright:template-favorites';

function useFavorites() {
  const [favorites, setFavorites] = React.useState<string[]>([]);
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem(FAVORITES_KEY);
      if (raw) setFavorites(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }, []);
  const toggle = React.useCallback((id: string) => {
    setFavorites((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      try {
        localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);
  return { favorites, toggle };
}

/** Mount children only once they scroll into view (gallery virtualization). */
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
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShown(true);
          io.disconnect();
        }
      },
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

function AtsStars({ score }: { score: number }) {
  return (
    <span
      className="inline-flex items-center gap-0.5"
      aria-label={`ATS compatibility ${score} out of 5`}
      title={`ATS compatibility ${score}/5`}
    >
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          className="h-3 w-3"
          fill={i <= score ? 'var(--at-warning)' : 'none'}
          stroke="var(--at-warning)"
          aria-hidden
        />
      ))}
    </span>
  );
}

/** A non-interactive, real render of the template's first page. */
function TemplateThumbnail({ template, data }: { template: ResumeTemplate; data: ResumeData }) {
  const settings = React.useMemo(() => templateToSettings(template), [template]);
  return (
    <div
      inert
      aria-hidden
      className="pointer-events-none overflow-hidden rounded-t-[var(--radius-at-lg)] bg-white"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      <ResumeDocument data={data} settings={settings} maxPages={1} />
    </div>
  );
}

export interface TemplateGalleryProps {
  onSelect?: (template: ResumeTemplate) => void;
  selectedId?: string;
  recommendSignal?: RecommendSignal;
  sampleData?: ResumeData;
  /**
   * Sample data used for photo-capable templates so their photo slot is
   * demonstrated in the preview. Defaults to the photo-enabled sample; a caller
   * rendering a real resume can pass their own data here to keep the two in sync.
   */
  samplePhotoData?: ResumeData;
  ctaLabel?: string;
}

export function TemplateGallery({
  onSelect,
  selectedId,
  recommendSignal,
  sampleData = SAMPLE_RESUME,
  samplePhotoData = SAMPLE_RESUME_WITH_PHOTO,
  ctaLabel = 'Use template',
}: TemplateGalleryProps) {
  // Photo-capable templates preview with a sample headshot so the photo slot is
  // visible; photo-incapable templates (e.g. latex) stay photo-less.
  const dataForTemplate = React.useCallback(
    (t: ResumeTemplate): ResumeData =>
      t.photoSupport !== 'none' ? samplePhotoData : sampleData,
    [sampleData, samplePhotoData]
  );
  const { favorites, toggle } = useFavorites();
  const [query, setQuery] = React.useState('');
  const [category, setCategory] = React.useState<TemplateCategory | 'all'>('all');
  const [photo, setPhoto] = React.useState<'all' | 'with-photo' | 'no-photo'>('all');
  const [atsOnly, setAtsOnly] = React.useState(false);
  const [favOnly, setFavOnly] = React.useState(false);
  const [sort, setSort] = React.useState<TemplateSort>('recommended');
  const [preview, setPreview] = React.useState<ResumeTemplate | null>(null);

  const recommendedIds = React.useMemo(() => {
    if (!recommendSignal) return new Set<string>();
    return new Set(
      recommendTemplates(recommendSignal, RESUME_TEMPLATES, 4).map((r) => r.template.id)
    );
  }, [recommendSignal]);

  const visible = React.useMemo(() => {
    let list = filterTemplates(RESUME_TEMPLATES, {
      query,
      category,
      photo,
      minAts: atsOnly ? 5 : undefined,
    });
    if (favOnly) list = list.filter((t) => favorites.includes(t.id));
    return sortTemplates(list, sort);
  }, [query, category, photo, atsOnly, favOnly, favorites, sort]);

  const handleUse = (t: ResumeTemplate) => {
    onSelect?.(t);
    setPreview(null);
  };

  return (
    <div className="space-y-5">
      {/* Controls */}
      <div className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search templates - role, industry, style, country..."
            aria-label="Search templates"
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

        <div className="flex flex-wrap items-center gap-2">
          <ToggleChip
            active={photo === 'with-photo'}
            onClick={() => setPhoto(photo === 'with-photo' ? 'all' : 'with-photo')}
          >
            <ImageIcon className="h-3.5 w-3.5" /> With photo
          </ToggleChip>
          <ToggleChip
            active={photo === 'no-photo'}
            onClick={() => setPhoto(photo === 'no-photo' ? 'all' : 'no-photo')}
          >
            No photo
          </ToggleChip>
          <ToggleChip active={atsOnly} onClick={() => setAtsOnly((v) => !v)}>
            Top ATS
          </ToggleChip>
          <ToggleChip active={favOnly} onClick={() => setFavOnly((v) => !v)}>
            <Star className="h-3.5 w-3.5" /> Favorites
          </ToggleChip>
          <div className="ml-auto flex items-center gap-2 text-sm">
            <label htmlFor="tpl-sort" className="text-[var(--muted-foreground)]">
              Sort
            </label>
            <select
              id="tpl-sort"
              value={sort}
              onChange={(e) => setSort(e.target.value as TemplateSort)}
              className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm"
            >
              <option value="recommended">Recommended</option>
              <option value="popular">Most popular</option>
              <option value="ats">Best ATS</option>
              <option value="name">Name (A-Z)</option>
            </select>
          </div>
        </div>
      </div>

      <p className="text-sm text-[var(--muted-foreground)]" role="status">
        {visible.length} template{visible.length === 1 ? '' : 's'}
      </p>

      {/* Grid */}
      {visible.length === 0 ? (
        <p className="py-12 text-center text-sm text-[var(--muted-foreground)]">
          No templates match your filters. Try clearing them.
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((t) => (
            <li key={t.id}>
              <TemplateCard
                template={t}
                data={dataForTemplate(t)}
                selected={selectedId === t.id}
                favorite={favorites.includes(t.id)}
                recommended={recommendedIds.has(t.id)}
                onToggleFavorite={() => toggle(t.id)}
                onPreview={() => setPreview(t)}
                onUse={() => handleUse(t)}
                ctaLabel={ctaLabel}
              />
            </li>
          ))}
        </ul>
      )}

      {/* Preview dialog */}
      <Dialog open={preview !== null} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent className="max-w-3xl">
          {preview && (
            <>
              <DialogHeader>
                <DialogTitle>{preview.name}</DialogTitle>
                <DialogDescription>{preview.description}</DialogDescription>
              </DialogHeader>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{categoryLabel(preview.category)}</Badge>
                <AtsStars score={preview.atsScore} />
                <Badge variant={preview.photoSupport === 'none' ? 'neutral' : 'primary'}>
                  {photoLabel(preview.photoSupport)}
                </Badge>
              </div>
              <p className="text-xs text-[var(--muted-foreground)]">{preview.atsNote}</p>
              <div className="max-h-[60vh] overflow-y-auto rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-4">
                <ResumeDocument
                  data={dataForTemplate(preview)}
                  settings={templateToSettings(preview)}
                  maxPages={2}
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => setPreview(null)}>
                  Close
                </Button>
                <Button onClick={() => handleUse(preview)}>{ctaLabel}</Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function categoryLabel(id: TemplateCategory): string {
  return TEMPLATE_CATEGORIES.find((c) => c.id === id)?.label ?? id;
}

function photoLabel(support: ResumeTemplate['photoSupport']): string {
  return support === 'none' ? 'No photo' : support === 'required' ? 'Photo' : 'Photo optional';
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

function ToggleChip({
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
        'inline-flex items-center gap-1 rounded-full border px-3 py-1 text-sm transition-colors',
        active
          ? 'border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]'
          : 'border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--primary)]'
      )}
    >
      {children}
    </button>
  );
}

interface TemplateCardProps {
  template: ResumeTemplate;
  data: ResumeData;
  selected: boolean;
  favorite: boolean;
  recommended: boolean;
  onToggleFavorite: () => void;
  onPreview: () => void;
  onUse: () => void;
  ctaLabel: string;
}

function TemplateCard({
  template,
  data,
  selected,
  favorite,
  recommended,
  onToggleFavorite,
  onPreview,
  onUse,
  ctaLabel,
}: TemplateCardProps) {
  return (
    <div
      className={cn(
        'group flex h-full flex-col overflow-hidden rounded-[var(--radius-at-lg)] border bg-[var(--card)] transition-shadow hover:shadow-[var(--shadow-at-e2)]',
        selected
          ? 'border-[var(--primary)] ring-2 ring-[var(--primary)]/30'
          : 'border-[var(--border)]'
      )}
    >
      <div className="relative">
        <button
          type="button"
          onClick={onPreview}
          className="block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          aria-label={`Preview ${template.name}`}
        >
          <LazyMount minHeight={220}>
            <TemplateThumbnail template={template} data={data} />
          </LazyMount>
        </button>
        <div className="absolute left-2 top-2 flex gap-1">
          {recommended && <Badge variant="ai">Recommended</Badge>}
          {selected && (
            <Badge variant="success">
              <Check className="h-3 w-3" /> Selected
            </Badge>
          )}
        </div>
        <button
          type="button"
          onClick={onToggleFavorite}
          aria-pressed={favorite}
          aria-label={
            favorite ? `Remove ${template.name} from favorites` : `Favorite ${template.name}`
          }
          className="absolute right-2 top-2 rounded-full bg-[var(--card)]/90 p-1.5 shadow-sm"
        >
          <Star
            className="h-4 w-4"
            fill={favorite ? 'var(--at-warning)' : 'none'}
            stroke="var(--at-warning)"
          />
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold leading-tight">{template.name}</h3>
          <AtsStars score={template.atsScore} />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="outline">{categoryLabel(template.category)}</Badge>
          <Badge variant={template.photoSupport === 'none' ? 'neutral' : 'primary'}>
            {photoLabel(template.photoSupport)}
          </Badge>
        </div>
        <p className="line-clamp-2 text-xs text-[var(--muted-foreground)]">
          {template.description}
        </p>
        <div className="mt-auto flex gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onPreview} className="flex-1">
            Preview
          </Button>
          <Button size="sm" onClick={onUse} className="flex-1">
            {ctaLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default TemplateGallery;
