'use client';

import { Button } from '@/components/atelier/button';
import { Check, X, Briefcase, FolderKanban } from 'lucide-react';
import type { EnhancedDescription } from '@/lib/api/enrichment';
import { useTranslations } from '@/lib/i18n';

interface PreviewStepProps {
  enhancements: EnhancedDescription[];
  onApply: () => void;
  onCancel: () => void;
}

export function PreviewStep({ enhancements, onApply, onCancel }: PreviewStepProps) {
  const { t } = useTranslations();
  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="mb-2 text-2xl font-semibold text-[var(--foreground)]">
          {t('enrichment.preview.title')}
        </h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          {t('enrichment.preview.description')}
        </p>
      </div>

      {/* Enhancements list */}
      <div className="flex-1 space-y-6 overflow-y-auto pr-2">
        {enhancements.map((enhancement) => (
          <EnhancementCard key={enhancement.item_id} enhancement={enhancement} />
        ))}
      </div>

      {/* Actions */}
      <div className="mt-6 flex items-center justify-between border-t border-[var(--border)] pt-6">
        <Button variant="outline" onClick={onCancel} className="gap-2">
          <X className="h-4 w-4" />
          {t('common.cancel')}
        </Button>
        <Button onClick={onApply} className="gap-2">
          <Check className="h-4 w-4" />
          {t('enrichment.preview.applyButton')}
        </Button>
      </div>
    </div>
  );
}

interface EnhancementCardProps {
  enhancement: EnhancedDescription;
}

function EnhancementCard({ enhancement }: EnhancementCardProps) {
  const { t } = useTranslations();
  const itemTypeLabel =
    enhancement.item_type === 'experience'
      ? t('enrichment.itemType.experience')
      : t('enrichment.itemType.project');

  return (
    <div className="overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-at-e1)]">
      {/* Card header */}
      <div className="flex items-center gap-2 border-b border-[var(--border)] bg-[var(--secondary)] px-4 py-3">
        {enhancement.item_type === 'experience' ? (
          <Briefcase className="h-4 w-4" />
        ) : (
          <FolderKanban className="h-4 w-4" />
        )}
        <span className="text-sm font-semibold uppercase tracking-wide">{itemTypeLabel}</span>
        <span className="text-[var(--muted-foreground)]">|</span>
        <span className="font-semibold">{enhancement.title}</span>
      </div>

      {/* Content preview */}
      <div className="p-4">
        <div className="space-y-4">
          {/* Existing bullets - keeping */}
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-semibold uppercase text-[var(--muted-foreground)]">
                {t('enrichment.preview.keepingLabel')}
              </span>
              <span className="text-xs text-[var(--muted-foreground)]">
                {t('enrichment.preview.existingCount', {
                  count: enhancement.original_description.length,
                })}
              </span>
            </div>
            <ul className="space-y-2">
              {enhancement.original_description.map((bullet, i) => (
                <li key={i} className="pl-4 text-sm text-[var(--muted-foreground)]">
                  {bullet}
                </li>
              ))}
              {enhancement.original_description.length === 0 && (
                <li className="text-sm italic text-[var(--muted-foreground)]">
                  {t('enrichment.preview.noExistingDescription')}
                </li>
              )}
            </ul>
          </div>

          {/* New bullets - adding */}
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-semibold uppercase text-[var(--at-success)]">
                {t('enrichment.preview.addingLabel')}
              </span>
              <span className="text-xs text-[var(--at-success)]">
                {t('enrichment.preview.newCount', {
                  count: enhancement.enhanced_description.length,
                })}
              </span>
            </div>
            <ul className="space-y-2">
              {enhancement.enhanced_description.map((bullet, i) => (
                <li
                  key={i}
                  className="rounded-[var(--radius-at-sm)] border border-[var(--at-success)]/40 bg-[var(--at-success)]/8 py-1 pl-4 pr-2 text-sm text-[var(--foreground)]"
                >
                  {bullet}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
