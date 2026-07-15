'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { Linkedin, Mail } from 'lucide-react';
import { useTranslations } from '@/lib/i18n';

export interface OutreachPreviewProps {
  /** Outreach message content */
  content: string;
  /** Additional class names */
  className?: string;
}

export function OutreachPreview({ content, className }: OutreachPreviewProps) {
  const { t } = useTranslations();
  return (
    <div
      className={cn(
        'overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-at-e1)]',
        className
      )}
    >
      {/* Preview Header */}
      <div className="border-b border-[var(--border)] bg-[var(--secondary)]/40 p-4">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Linkedin className="h-4 w-4 text-[#0077B5]" />
            <span className="text-xs uppercase text-[var(--muted-foreground)]">
              {t('outreach.preview.channels.linkedin')}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-[var(--muted-foreground)]" />
            <span className="text-xs uppercase text-[var(--muted-foreground)]">
              {t('outreach.preview.channels.email')}
            </span>
          </div>
        </div>
      </div>

      {/* Message Preview */}
      <div className="p-6 md:p-8">
        {content ? (
          <div className="space-y-4">
            {/* Message Bubble Style */}
            <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--secondary)]/40 p-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--foreground)]">
                {content}
              </p>
            </div>

            {/* Usage Tips */}
            <div className="border-t border-[var(--border)] pt-4">
              <p className="mb-2 text-xs uppercase text-[var(--muted-foreground)]">
                {t('outreach.preview.howToUseTitle')}
              </p>
              <ul className="space-y-1 text-xs text-[var(--muted-foreground)]">
                <li>{t('outreach.preview.steps.step1')}</li>
                <li>{t('outreach.preview.steps.step2')}</li>
                <li>{t('outreach.preview.steps.step3')}</li>
                <li>{t('outreach.preview.steps.step4')}</li>
              </ul>
            </div>
          </div>
        ) : (
          <div className="py-12 text-center text-[var(--muted-foreground)]">
            <p className="text-sm">{t('outreach.preview.emptyTitle')}</p>
            <p className="mt-2 text-xs">{t('outreach.preview.emptyDescription')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
