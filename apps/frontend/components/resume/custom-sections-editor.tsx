'use client';

/**
 * CustomSectionsEditor - section ordering/visibility + custom sections, ported
 * into the atelier resume editor (previously only in the legacy /builder tree).
 * Self-contained (reuses only the pure section-helpers utils) so the legacy tree
 * can be retired without touching the atelier editor.
 *
 * Operates on `sectionMeta` (order + visibility, drives the render engine's
 * getSortedSections) and `customSections` (the content bags). It surfaces:
 *  - reorder any section up/down (personalInfo stays pinned first),
 *  - show/hide any section,
 *  - add / rename / delete custom sections (text, list, or entries),
 *  - edit custom-section content by type.
 * All changes are emitted via `onChange`; the parent owns dirty-state + save.
 */
import * as React from 'react';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Trash from 'lucide-react/dist/esm/icons/trash-2';
import ArrowUp from 'lucide-react/dist/esm/icons/arrow-up';
import ArrowDown from 'lucide-react/dist/esm/icons/arrow-down';
import Eye from 'lucide-react/dist/esm/icons/eye';
import EyeOff from 'lucide-react/dist/esm/icons/eye-off';
import Layers from 'lucide-react/dist/esm/icons/layers';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { cn } from '@/lib/utils';
import { createCustomSection, getAllSections } from '@/lib/utils/section-helpers';
import type {
  SectionMeta,
  SectionType,
  CustomSection,
  CustomSectionItem,
} from '@/components/dashboard/resume-component';

type SelectableSectionType = Exclude<SectionType, 'personalInfo'>;

interface Props {
  sectionMeta: SectionMeta[];
  customSections: Record<string, CustomSection>;
  onChange: (next: {
    sectionMeta: SectionMeta[];
    customSections: Record<string, CustomSection>;
  }) => void;
}

const TYPE_LABEL: Record<SectionType, string> = {
  personalInfo: 'Contact',
  text: 'Text block',
  itemList: 'Entries',
  stringList: 'List',
};

function toLines(v: string): string[] {
  return v
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function CustomSectionsEditor({ sectionMeta, customSections, onChange }: Props) {
  const [addOpen, setAddOpen] = React.useState(false);

  const sorted = React.useMemo(() => getAllSections({ sectionMeta } as never), [sectionMeta]);

  function emit(nextMeta: SectionMeta[], nextCustom: Record<string, CustomSection>) {
    onChange({ sectionMeta: nextMeta, customSections: nextCustom });
  }

  function moveSection(id: string, dir: -1 | 1) {
    const arr = [...sorted];
    const idx = arr.findIndex((s) => s.id === id);
    const target = idx + dir;
    if (idx < 0 || target < 0 || target >= arr.length) return;
    // Keep personalInfo pinned at the top.
    if (arr[idx].id === 'personalInfo' || arr[target].id === 'personalInfo') return;
    [arr[idx], arr[target]] = [arr[target], arr[idx]];
    emit(
      arr.map((s, i) => ({ ...s, order: i })),
      customSections
    );
  }

  function toggleVisibility(id: string) {
    emit(
      sectionMeta.map((s) => (s.id === id ? { ...s, isVisible: !s.isVisible } : s)),
      customSections
    );
  }

  function rename(id: string, displayName: string) {
    emit(
      sectionMeta.map((s) => (s.id === id ? { ...s, displayName } : s)),
      customSections
    );
  }

  function addSection(displayName: string, type: SelectableSectionType) {
    const meta = createCustomSection(sorted, displayName, type);
    const bag: CustomSection =
      type === 'text'
        ? { sectionType: 'text', text: '' }
        : type === 'stringList'
          ? { sectionType: 'stringList', strings: [] }
          : { sectionType: 'itemList', items: [] };
    emit([...sectionMeta, meta], { ...customSections, [meta.key]: bag });
  }

  function deleteSection(id: string, key: string) {
    const nextCustom = { ...customSections };
    delete nextCustom[key];
    emit(
      sectionMeta.filter((s) => s.id !== id),
      nextCustom
    );
  }

  function updateContent(key: string, patch: Partial<CustomSection>) {
    const existing = customSections[key] ?? { sectionType: 'text' };
    emit(sectionMeta, { ...customSections, [key]: { ...existing, ...patch } });
  }

  const customMeta = sorted.filter((s) => !s.isDefault);

  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--muted-foreground)]">
          <Layers className="h-4 w-4" /> Sections
        </h2>
        <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" /> Add section
        </Button>
      </div>

      {/* Order + visibility for ALL sections */}
      <ul className="space-y-1.5">
        {sorted.map((s, i) => {
          const pinned = s.id === 'personalInfo';
          return (
            <li
              key={s.id}
              className="flex items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] px-2.5 py-1.5"
            >
              <span
                className={cn(
                  'min-w-0 flex-1 truncate text-sm',
                  s.isVisible ? 'text-[var(--foreground)]' : 'text-[var(--muted-foreground)]'
                )}
              >
                {s.displayName}
              </span>
              {/* Type hint is secondary - hide on the narrowest screens to keep
                  tap targets from crowding. */}
              <Badge variant="neutral" className="hidden shrink-0 sm:inline-flex">
                {TYPE_LABEL[s.sectionType]}
              </Badge>
              {!pinned && (
                <Button
                  variant="ghost"
                  size="iconSm"
                  aria-label={s.isVisible ? `Hide ${s.displayName}` : `Show ${s.displayName}`}
                  onClick={() => toggleVisibility(s.id)}
                  className="text-[var(--muted-foreground)]"
                >
                  {s.isVisible ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                </Button>
              )}
              <Button
                variant="ghost"
                size="iconSm"
                aria-label={`Move ${s.displayName} up`}
                disabled={pinned || i <= 1}
                onClick={() => moveSection(s.id, -1)}
                className="text-[var(--muted-foreground)]"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="iconSm"
                aria-label={`Move ${s.displayName} down`}
                disabled={pinned || i === sorted.length - 1}
                onClick={() => moveSection(s.id, 1)}
                className="text-[var(--muted-foreground)]"
              >
                <ArrowDown className="h-4 w-4" />
              </Button>
              {!s.isDefault && (
                <Button
                  variant="ghost"
                  size="iconSm"
                  aria-label={`Delete ${s.displayName}`}
                  onClick={() => deleteSection(s.id, s.key)}
                  className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                >
                  <Trash className="h-4 w-4" />
                </Button>
              )}
            </li>
          );
        })}
      </ul>

      {/* Content editors for custom sections */}
      {customMeta.map((s) => (
        <CustomSectionContent
          key={s.id}
          meta={s}
          section={customSections[s.key]}
          onRename={(name) => rename(s.id, name)}
          onUpdate={(patch) => updateContent(s.key, patch)}
        />
      ))}

      <AddSectionDialog open={addOpen} onOpenChange={setAddOpen} onAdd={addSection} />
    </Card>
  );
}

function CustomSectionContent({
  meta,
  section,
  onRename,
  onUpdate,
}: {
  meta: SectionMeta;
  section: CustomSection | undefined;
  onRename: (name: string) => void;
  onUpdate: (patch: Partial<CustomSection>) => void;
}) {
  const type = meta.sectionType;
  return (
    <div className="space-y-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
      <div className="space-y-1">
        <Label className="text-xs">Section name</Label>
        <Input value={meta.displayName} onChange={(e) => onRename(e.target.value)} />
      </div>

      {type === 'text' && (
        <div className="space-y-1">
          <Label className="text-xs">Content</Label>
          <Textarea
            value={section?.text ?? ''}
            onChange={(e) => onUpdate({ text: e.target.value })}
            className="min-h-24"
            placeholder={`Write the ${meta.displayName.toLowerCase()} content...`}
          />
        </div>
      )}

      {type === 'stringList' && (
        <div className="space-y-1">
          <Label className="text-xs">Items (one per line)</Label>
          <Textarea
            value={(section?.strings ?? []).join('\n')}
            onChange={(e) => onUpdate({ strings: toLines(e.target.value) })}
            className="min-h-24"
            placeholder={'Item one\nItem two'}
          />
        </div>
      )}

      {type === 'itemList' && (
        <ItemListEditor items={section?.items ?? []} onChange={(items) => onUpdate({ items })} />
      )}
    </div>
  );
}

function ItemListEditor({
  items,
  onChange,
}: {
  items: CustomSectionItem[];
  onChange: (items: CustomSectionItem[]) => void;
}) {
  function addItem() {
    const nextId = items.reduce((m, it) => Math.max(m, it.id), 0) + 1;
    onChange([...items, { id: nextId, title: '', subtitle: '', years: '', description: [] }]);
  }
  function updateItem(index: number, patch: Partial<CustomSectionItem>) {
    onChange(items.map((it, i) => (i === index ? { ...it, ...patch } : it)));
  }
  function removeItem(index: number) {
    onChange(items.filter((_, i) => i !== index));
  }
  function move(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= items.length) return;
    const arr = [...items];
    [arr[index], arr[target]] = [arr[target], arr[index]];
    onChange(arr);
  }

  return (
    <div className="space-y-2">
      <Label className="text-xs">Entries</Label>
      {items.map((it, i) => (
        <div
          key={it.id}
          className="space-y-2 rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] p-2.5"
        >
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              {i + 1} of {items.length}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="iconSm"
                aria-label="Move entry up"
                disabled={i === 0}
                onClick={() => move(i, -1)}
                className="text-[var(--muted-foreground)]"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="iconSm"
                aria-label="Move entry down"
                disabled={i === items.length - 1}
                onClick={() => move(i, 1)}
                className="text-[var(--muted-foreground)]"
              >
                <ArrowDown className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="iconSm"
                aria-label="Remove entry"
                onClick={() => removeItem(i)}
                className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
              >
                <Trash className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Input
              placeholder="Title"
              value={it.title ?? ''}
              onChange={(e) => updateItem(i, { title: e.target.value })}
            />
            <Input
              placeholder="Subtitle"
              value={it.subtitle ?? ''}
              onChange={(e) => updateItem(i, { subtitle: e.target.value })}
            />
          </div>
          <Input
            placeholder="Dates"
            value={it.years ?? ''}
            onChange={(e) => updateItem(i, { years: e.target.value })}
          />
          <Textarea
            placeholder="Details (one per line)"
            value={(it.description ?? []).join('\n')}
            onChange={(e) => updateItem(i, { description: toLines(e.target.value) })}
            className="min-h-16"
          />
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={addItem}>
        <Plus className="h-4 w-4" /> Add entry
      </Button>
    </div>
  );
}

function AddSectionDialog({
  open,
  onOpenChange,
  onAdd,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onAdd: (displayName: string, type: SelectableSectionType) => void;
}) {
  const [name, setName] = React.useState('');
  const [type, setType] = React.useState<SelectableSectionType>('text');

  const types: { type: SelectableSectionType; label: string; description: string }[] = [
    {
      type: 'text',
      label: 'Text block',
      description: 'A paragraph of free text (e.g. a statement).',
    },
    {
      type: 'itemList',
      label: 'Entries',
      description: 'A list of entries with title, dates, and details.',
    },
    { type: 'stringList', label: 'List', description: 'A simple bulleted list (e.g. interests).' },
  ];

  function submit() {
    if (!name.trim()) return;
    onAdd(name.trim(), type);
    setName('');
    setType('text');
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Add a custom section</DialogTitle>
          <DialogDescription>
            Add a section like Certifications, Publications, or Volunteering.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="cs-name">Section name</Label>
            <Input
              id="cs-name"
              value={name}
              autoFocus
              placeholder="e.g. Certifications"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && name.trim()) submit();
              }}
            />
          </div>
          <div className="space-y-2">
            <Label>Type</Label>
            {types.map((t) => (
              <button
                key={t.type}
                type="button"
                onClick={() => setType(t.type)}
                className={cn(
                  'w-full rounded-[var(--radius-at-md)] border p-3 text-left transition-colors',
                  type === t.type
                    ? 'border-[var(--primary)] bg-[var(--accent)]'
                    : 'border-[var(--border)] hover:bg-[var(--accent)]'
                )}
              >
                <div className="text-sm font-medium text-[var(--foreground)]">{t.label}</div>
                <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{t.description}</div>
              </button>
            ))}
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button onClick={submit} disabled={!name.trim()}>
            <Plus className="h-4 w-4" /> Add section
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
