'use client';

import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { Button } from '@/components/atelier/button';
import { Briefcase, FolderKanban, Lightbulb, ChevronDown, ChevronRight, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';
import type { RegenerateItemInput } from '@/lib/api/enrichment';

interface RegenerateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  experienceItems: RegenerateItemInput[];
  projectItems: RegenerateItemInput[];
  skillsItem: RegenerateItemInput | null;
  selectedItems: RegenerateItemInput[];
  onSelectionChange: (items: RegenerateItemInput[]) => void;
  onContinue: () => void;
}

const sectionHeaderCls =
  'flex w-full items-center justify-between p-4 transition-colors bg-[var(--card)] hover:bg-[var(--accent)]';

/**
 * RegenerateDialog Component — first step of the regenerate wizard. Lets the
 * user select which resume items to regenerate.
 */
export const RegenerateDialog: React.FC<RegenerateDialogProps> = ({
  open,
  onOpenChange,
  experienceItems,
  projectItems,
  skillsItem,
  selectedItems,
  onSelectionChange,
  onContinue,
}) => {
  const { t } = useTranslations();
  const [expandedSections, setExpandedSections] = React.useState<Set<string>>(
    new Set(['experience', 'projects', 'skills'])
  );

  const toggleSection = (section: string) => {
    const newExpanded = new Set(expandedSections);
    if (newExpanded.has(section)) {
      newExpanded.delete(section);
    } else {
      newExpanded.add(section);
    }
    setExpandedSections(newExpanded);
  };

  const isSelected = (item: RegenerateItemInput) => {
    return selectedItems.some((s) => s.item_id === item.item_id);
  };

  const toggleItem = (item: RegenerateItemInput) => {
    if (isSelected(item)) {
      onSelectionChange(selectedItems.filter((s) => s.item_id !== item.item_id));
    } else {
      onSelectionChange([...selectedItems, item]);
    }
  };

  const hasItems = experienceItems.length > 0 || projectItems.length > 0 || skillsItem !== null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>{t('builder.regenerate.selectDialog.title')}</DialogTitle>
          <DialogDescription>{t('builder.regenerate.selectDialog.subtitle')}</DialogDescription>
        </DialogHeader>

        <div className="max-h-[50vh] space-y-4 overflow-y-auto">
          {!hasItems && (
            <div className="py-8 text-center text-sm text-[var(--muted-foreground)]">
              {t('builder.regenerate.selectDialog.noItemsAvailable')}
            </div>
          )}

          {/* Experience Section */}
          {experienceItems.length > 0 && (
            <div className="overflow-hidden rounded-[var(--radius-at-md)] border border-[var(--border)]">
              <button
                type="button"
                onClick={() => toggleSection('experience')}
                aria-expanded={expandedSections.has('experience')}
                className={sectionHeaderCls}
              >
                <div className="flex items-center gap-3">
                  <Briefcase className="h-5 w-5" />
                  <span className="text-sm font-medium">
                    {t('builder.regenerate.selectDialog.experience')}
                  </span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    ({experienceItems.length})
                  </span>
                </div>
                {expandedSections.has('experience') ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </button>
              {expandedSections.has('experience') && (
                <div className="border-t border-[var(--border)]">
                  {experienceItems.map((item) => (
                    <ItemRow
                      key={item.item_id}
                      item={item}
                      isSelected={isSelected(item)}
                      onToggle={() => toggleItem(item)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Projects Section */}
          {projectItems.length > 0 && (
            <div className="overflow-hidden rounded-[var(--radius-at-md)] border border-[var(--border)]">
              <button
                type="button"
                onClick={() => toggleSection('projects')}
                aria-expanded={expandedSections.has('projects')}
                className={sectionHeaderCls}
              >
                <div className="flex items-center gap-3">
                  <FolderKanban className="h-5 w-5" />
                  <span className="text-sm font-medium">
                    {t('builder.regenerate.selectDialog.projects')}
                  </span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    ({projectItems.length})
                  </span>
                </div>
                {expandedSections.has('projects') ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </button>
              {expandedSections.has('projects') && (
                <div className="border-t border-[var(--border)]">
                  {projectItems.map((item) => (
                    <ItemRow
                      key={item.item_id}
                      item={item}
                      isSelected={isSelected(item)}
                      onToggle={() => toggleItem(item)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Skills Section */}
          {skillsItem && (
            <div className="overflow-hidden rounded-[var(--radius-at-md)] border border-[var(--border)]">
              <button
                type="button"
                onClick={() => toggleSection('skills')}
                aria-expanded={expandedSections.has('skills')}
                className={sectionHeaderCls}
              >
                <div className="flex items-center gap-3">
                  <Lightbulb className="h-5 w-5" />
                  <span className="text-sm font-medium">
                    {t('builder.regenerate.selectDialog.skills')}
                  </span>
                </div>
                {expandedSections.has('skills') ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </button>
              {expandedSections.has('skills') && (
                <div className="border-t border-[var(--border)]">
                  <ItemRow
                    item={skillsItem}
                    isSelected={isSelected(skillsItem)}
                    onToggle={() => toggleItem(skillsItem)}
                  />
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{t('common.cancel')}</Button>
          </DialogClose>
          <Button onClick={onContinue} disabled={selectedItems.length === 0}>
            {t('builder.regenerate.selectDialog.continueButton')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

/**
 * ItemRow - Individual selectable item row
 */
interface ItemRowProps {
  item: RegenerateItemInput;
  isSelected: boolean;
  onToggle: () => void;
}

const ItemRow: React.FC<ItemRowProps> = ({ item, isSelected, onToggle }) => {
  const { t } = useTranslations();

  const contentCount = item.current_content.length;
  const itemCountKey =
    contentCount === 1
      ? 'builder.regenerate.selectDialog.itemCount.one'
      : 'builder.regenerate.selectDialog.itemCount.other';
  const itemCountLabel = t(itemCountKey).replace('{count}', String(contentCount));

  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        'flex w-full items-center gap-4 p-4 text-left transition-colors',
        isSelected ? 'bg-[var(--accent)]' : 'bg-[var(--card)] hover:bg-[var(--accent)]'
      )}
    >
      {/* Checkbox */}
      <div
        className={cn(
          'flex h-5 w-5 items-center justify-center rounded-[var(--radius-at-sm)] border transition-colors',
          isSelected
            ? 'border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]'
            : 'border-[var(--border)] bg-[var(--card)]'
        )}
      >
        {isSelected && <Check className="h-3 w-3" />}
      </div>

      {/* Item Info */}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{item.title}</div>
        {item.subtitle && (
          <div className="truncate text-xs text-[var(--muted-foreground)]">{item.subtitle}</div>
        )}
      </div>

      {/* Content preview */}
      <div className="text-xs text-[var(--muted-foreground)]">{itemCountLabel}</div>
    </button>
  );
};

export default RegenerateDialog;
