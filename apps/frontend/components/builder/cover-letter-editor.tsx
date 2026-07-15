'use client';

import * as React from 'react';
import { Button } from '@/components/atelier/button';
import { Save, Loader2, FileText } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';

export interface CoverLetterEditorProps {
  /** Cover letter content */
  content: string;
  /** Callback when content changes */
  onChange: (content: string) => void;
  /** Callback when save is triggered */
  onSave: () => void;
  /** Whether save is in progress */
  isSaving: boolean;
  /** Additional class names */
  className?: string;
}

export function CoverLetterEditor({
  content,
  onChange,
  onSave,
  isSaving,
  className,
}: CoverLetterEditorProps) {
  const { t } = useTranslations();
  const wordCount = content
    .trim()
    .split(/\s+/)
    .filter((w) => w.length > 0).length;
  const charCount = content.length;

  return (
    <div className={cn('flex h-full flex-col', className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--secondary)]/40 p-4">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4" />
          <h2 className="text-sm font-semibold text-[var(--foreground)]">
            {t('coverLetter.title')}
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[var(--muted-foreground)]">
            {t('builder.contentStats.wordsChars', { wordCount, charCount })}
          </span>
          <Button size="sm" onClick={onSave} disabled={isSaving}>
            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {isSaving ? t('common.saving') : t('common.save')}
          </Button>
        </div>
      </div>

      {/* Editor Area */}
      <div className="flex-1 overflow-hidden p-4">
        <textarea
          value={content}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t('coverLetter.editor.placeholder')}
          className={cn(
            'h-full min-h-[400px] w-full resize-none p-4 text-sm leading-relaxed',
            'rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]',
            'placeholder:text-[var(--muted-foreground)]',
            'focus-visible:border-[var(--ring)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20'
          )}
        />
      </div>

      {/* Footer Tips */}
      <div className="border-t border-[var(--border)] bg-[var(--secondary)]/40 p-4">
        <p className="text-xs text-[var(--muted-foreground)]">{t('coverLetter.editor.tip')}</p>
      </div>
    </div>
  );
}
