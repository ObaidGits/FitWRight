'use client';

import * as React from 'react';
import { Button } from '@/components/atelier/button';
import { Save, Loader2, Copy, Check, Mail } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';

export interface OutreachEditorProps {
  /** Outreach message content */
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

export function OutreachEditor({
  content,
  onChange,
  onSave,
  isSaving,
  className,
}: OutreachEditorProps) {
  const { t } = useTranslations();
  const [isCopied, setIsCopied] = React.useState(false);

  const wordCount = content
    .trim()
    .split(/\s+/)
    .filter((w) => w.length > 0).length;
  const charCount = content.length;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <div className={cn('flex h-full flex-col', className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--secondary)]/40 p-4">
        <div className="flex items-center gap-2">
          <Mail className="h-4 w-4" />
          <h2 className="text-sm font-semibold text-[var(--foreground)]">{t('outreach.title')}</h2>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[var(--muted-foreground)]">
            {t('builder.contentStats.wordsChars', { wordCount, charCount })}
          </span>
          <Button size="sm" variant="outline" onClick={onSave} disabled={isSaving}>
            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {isSaving ? t('common.saving') : t('common.save')}
          </Button>
          <Button size="sm" onClick={handleCopy} disabled={!content}>
            {isCopied ? (
              <>
                <Check className="h-4 w-4" />
                {t('outreach.copied')}
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" />
                {t('outreach.copy')}
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Editor Area */}
      <div className="flex-1 overflow-hidden p-4">
        <textarea
          value={content}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t('outreach.editor.placeholder')}
          className={cn(
            'h-full min-h-[250px] w-full resize-none p-4 text-sm leading-relaxed',
            'rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]',
            'placeholder:text-[var(--muted-foreground)]',
            'focus-visible:border-[var(--ring)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20'
          )}
        />
      </div>

      {/* Footer Tips */}
      <div className="border-t border-[var(--border)] bg-[var(--secondary)]/40 p-4">
        <p className="text-xs text-[var(--muted-foreground)]">{t('outreach.editor.tip')}</p>
      </div>
    </div>
  );
}
