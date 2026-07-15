'use client';

import * as React from 'react';
import { Button } from '@/components/atelier/button';
import { Sparkles, Loader2, FileText, Mail, MessagesSquare, ArrowRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';

export interface GeneratePromptProps {
  /** Type of content to generate */
  type: 'cover-letter' | 'outreach' | 'interview-prep';
  /** Whether generation is in progress */
  isGenerating: boolean;
  /** Callback to trigger generation */
  onGenerate: () => void;
  /** Whether this is a tailored resume (has job context) */
  isTailoredResume: boolean;
  /** Additional class names */
  className?: string;
}

export function GeneratePrompt({
  type,
  isGenerating,
  onGenerate,
  isTailoredResume,
  className,
}: GeneratePromptProps) {
  const { t } = useTranslations();
  const isOutreach = type === 'outreach';
  const isInterviewPrep = type === 'interview-prep';
  const Icon = isInterviewPrep ? MessagesSquare : isOutreach ? Mail : FileText;
  const title = isInterviewPrep
    ? t('interviewPrep.title')
    : isOutreach
      ? t('outreach.title')
      : t('coverLetter.title');

  // Show a different message if resume is not tailored
  if (!isTailoredResume) {
    return (
      <div
        className={cn(
          'flex min-h-[400px] flex-col items-center justify-center p-12 text-center',
          className
        )}
      >
        <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-[var(--secondary)] text-[var(--muted-foreground)]">
          <Icon className="h-8 w-8" />
        </div>
        <h3 className="mb-3 text-base font-semibold text-[var(--foreground)]">
          {t('builder.generatePrompt.notAvailableTitle', { title })}
        </h3>
        <p className="mb-6 max-w-md text-sm leading-relaxed text-[var(--muted-foreground)]">
          {t('builder.generatePrompt.notAvailableDescription', { title })}
        </p>
        <div className="flex items-center gap-2 text-sm text-[var(--primary)]">
          <span>{t('builder.generatePrompt.goToDashboard')}</span>
          <ArrowRight className="h-4 w-4" />
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex min-h-[400px] flex-col items-center justify-center p-12 text-center',
        className
      )}
    >
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-[var(--primary)]/12 text-[var(--primary)]">
        <Icon className="h-8 w-8" />
      </div>
      <h3 className="mb-3 text-base font-semibold text-[var(--foreground)]">
        {t('builder.generatePrompt.generateTitle', { title })}
      </h3>
      <p className="mb-6 max-w-md text-sm leading-relaxed text-[var(--muted-foreground)]">
        {isInterviewPrep
          ? t('builder.generatePrompt.interviewPrepDescription')
          : isOutreach
            ? t('builder.generatePrompt.outreachDescription')
            : t('builder.generatePrompt.coverLetterDescription')}
      </p>
      <Button onClick={onGenerate} disabled={isGenerating} className="gap-2">
        {isGenerating ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('common.generating')}
          </>
        ) : (
          <>
            <Sparkles className="h-4 w-4" />
            {t('builder.generatePrompt.generateButton', { title })}
          </>
        )}
      </Button>
      <p className="mt-4 text-xs text-[var(--muted-foreground)]">
        {isInterviewPrep
          ? t('builder.generatePrompt.interviewPrepFooter')
          : isOutreach
            ? t('builder.generatePrompt.outreachFooter')
            : t('builder.generatePrompt.coverLetterFooter')}
      </p>
    </div>
  );
}
