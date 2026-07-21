'use client';

import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/atelier/dialog';
import { Button } from '@/components/atelier/button';
import {
  Check,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Briefcase,
  FolderKanban,
  Lightbulb,
} from 'lucide-react';
import { useTranslations } from '@/lib/i18n';
import type { RegenerateItemError, RegeneratedItem } from '@/lib/api/enrichment';

interface RegenerateDiffPreviewProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  regeneratedItems: RegeneratedItem[];
  regenerateErrors?: RegenerateItemError[];
  error: string | null;
  onAccept: () => void;
  onReject: () => void;
  isApplying: boolean;
}

/**
 * RegenerateDiffPreview Component - third step of the regenerate wizard. Shows a
 * side-by-side comparison of original vs regenerated content.
 */
export const RegenerateDiffPreview: React.FC<RegenerateDiffPreviewProps> = ({
  open,
  onOpenChange,
  regeneratedItems,
  regenerateErrors = [],
  error,
  onAccept,
  onReject,
  isApplying,
}) => {
  const { t } = useTranslations();
  const [expandedItems, setExpandedItems] = React.useState<Set<string>>(
    new Set(regeneratedItems.map((item) => item.item_id))
  );

  React.useEffect(() => {
    // Expand all items when regeneratedItems changes
    setExpandedItems(new Set(regeneratedItems.map((item) => item.item_id)));
  }, [regeneratedItems]);

  const toggleItem = (itemId: string) => {
    const newExpanded = new Set(expandedItems);
    if (newExpanded.has(itemId)) {
      newExpanded.delete(itemId);
    } else {
      newExpanded.add(itemId);
    }
    setExpandedItems(newExpanded);
  };

  type ItemLabelSource = Pick<RegeneratedItem, 'item_id' | 'item_type' | 'title' | 'subtitle'>;

  const getItemLabel = (item: ItemLabelSource) => {
    if (item.item_type === 'skills') {
      return t('builder.regenerate.selectDialog.skills');
    }

    const title = item.title?.trim();
    const subtitle = item.subtitle?.trim();

    if (title && subtitle) {
      return `${title} | ${subtitle}`;
    }

    return title || item.item_id;
  };

  const getItemIcon = (itemType: string) => {
    switch (itemType) {
      case 'experience':
        return <Briefcase className="h-4 w-4" />;
      case 'project':
        return <FolderKanban className="h-4 w-4" />;
      case 'skills':
        return <Lightbulb className="h-4 w-4" />;
      default:
        return null;
    }
  };

  const resolveErrorMessage = (value: string) => {
    if (value === 'No changes to apply') {
      return t('builder.regenerate.errors.noChangesToApply');
    }

    if (/network|fetch/i.test(value) || value.includes('Failed to fetch')) {
      return t('builder.regenerate.errors.networkError');
    }

    if (/resume content changed|uniquely matched|please regenerate/i.test(value)) {
      return t('builder.regenerate.errors.resumeChanged');
    }

    return t('builder.regenerate.errors.applyFailed');
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-[800px]">
        <DialogHeader>
          <DialogTitle>{t('builder.regenerate.diffPreview.title')}</DialogTitle>
          <DialogDescription>{t('builder.regenerate.diffPreview.subtitle')}</DialogDescription>
        </DialogHeader>

        {/* Stats Card */}
        <div className="inline-flex items-center gap-2 self-start rounded-[var(--radius-at-sm)] border border-[var(--at-success)]/40 bg-[var(--at-success)]/10 px-3 py-1 text-xs text-[var(--at-success)]">
          <Check className="h-3 w-3" />
          {t('builder.regenerate.diffPreview.changesCount').replace(
            '{count}',
            String(regeneratedItems.length)
          )}
        </div>

        {error ? (
          <div className="rounded-[var(--radius-at-md)] border border-[var(--destructive)]/40 bg-[var(--destructive)]/8 px-4 py-3">
            <p className="text-xs text-[var(--destructive)]">{resolveErrorMessage(error)}</p>
          </div>
        ) : null}

        {regenerateErrors.length > 0 ? (
          <div className="rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 px-4 py-3">
            <p className="text-xs text-[var(--foreground)]">
              {t('builder.regenerate.diffPreview.partialFailures', {
                count: regenerateErrors.length,
              })}
            </p>
            <ul className="mt-2 space-y-1">
              {regenerateErrors.map((failed) => (
                <li key={failed.item_id} className="text-xs text-[var(--muted-foreground)]">
                  - {getItemLabel(failed)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* Diff Content */}
        <div className="max-h-[50vh] space-y-4 overflow-y-auto">
          {regeneratedItems.map((item) => (
            <div
              key={item.item_id}
              className="overflow-hidden rounded-[var(--radius-at-md)] border border-[var(--border)]"
            >
              {/* Item Header */}
              <button
                type="button"
                onClick={() => toggleItem(item.item_id)}
                aria-expanded={expandedItems.has(item.item_id)}
                aria-label={
                  expandedItems.has(item.item_id)
                    ? t('builder.regenerate.diffPreview.collapseItem', { item: getItemLabel(item) })
                    : t('builder.regenerate.diffPreview.expandItem', { item: getItemLabel(item) })
                }
                className="flex w-full items-center justify-between bg-[var(--card)] p-4 transition-colors hover:bg-[var(--accent)]"
              >
                <div className="flex items-center gap-3">
                  {getItemIcon(item.item_type)}
                  <span className="truncate text-sm font-medium">{getItemLabel(item)}</span>
                </div>
                {expandedItems.has(item.item_id) ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </button>

              {/* Item Diff Content */}
              {expandedItems.has(item.item_id) && (
                <div className="border-t border-[var(--border)]">
                  {/* Change Summary */}
                  {item.diff_summary && (
                    <div className="border-b border-[var(--border)] p-3">
                      <p className="text-xs text-[var(--primary)]">{item.diff_summary}</p>
                    </div>
                  )}

                  {/* Original Content */}
                  <div className="border-b border-[var(--border)] p-4">
                    <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      <span className="h-3 w-3 rounded-full bg-[var(--destructive)]" />
                      {t('builder.regenerate.diffPreview.originalLabel')}
                    </div>
                    <div className="space-y-1 rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-[var(--card)] p-3">
                      {item.original_content.length > 0 ? (
                        item.original_content.map((content, idx) => (
                          <p key={idx} className="text-sm text-[var(--destructive)] line-through">
                            <span className="mr-2">−</span>
                            {content}
                          </p>
                        ))
                      ) : (
                        <p className="text-sm italic text-[var(--muted-foreground)]">
                          {t('builder.regenerate.diffPreview.noContent')}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* New Content */}
                  <div className="p-4">
                    <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      <span className="h-3 w-3 rounded-full bg-[var(--at-success)]" />
                      {t('builder.regenerate.diffPreview.newLabel')}
                    </div>
                    <div className="space-y-1 rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-[var(--card)] p-3">
                      {item.new_content.length > 0 ? (
                        item.new_content.map((content, idx) => (
                          <p key={idx} className="text-sm text-[var(--at-success)]">
                            <span className="mr-2">+</span>
                            {content}
                          </p>
                        ))
                      ) : (
                        <p className="text-sm italic text-[var(--muted-foreground)]">
                          {t('builder.regenerate.diffPreview.noContent')}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        <DialogFooter className="justify-between">
          <Button variant="outline" onClick={onReject} disabled={isApplying}>
            <RefreshCw className="mr-2 h-4 w-4" />
            {t('builder.regenerate.diffPreview.rejectButton')}
          </Button>
          <Button variant="success" onClick={onAccept} disabled={isApplying}>
            {isApplying ? (
              <>
                <span className="mr-2 animate-spin">
                  <Check className="h-4 w-4" />
                </span>
                {t('builder.regenerate.diffPreview.applying')}
              </>
            ) : (
              <>
                <Check className="mr-2 h-4 w-4" />
                {t('builder.regenerate.diffPreview.acceptButton')}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RegenerateDiffPreview;
