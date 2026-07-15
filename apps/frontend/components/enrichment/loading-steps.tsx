'use client';

import { Loader2, CheckCircle2, Sparkles, AlertCircle } from 'lucide-react';
import { Button } from '@/components/atelier/button';
import { useTranslations } from '@/lib/i18n';

interface LoadingStepProps {
  message: string;
  submessage?: string;
}

function LoadingStep({ message, submessage }: LoadingStepProps) {
  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-6">
      <div className="relative">
        <Loader2 className="h-12 w-12 animate-spin text-[var(--primary)]" />
      </div>
      <div className="text-center">
        <p className="text-xl font-semibold text-[var(--foreground)]">{message}</p>
        {submessage && <p className="mt-2 text-sm text-[var(--muted-foreground)]">{submessage}</p>}
      </div>
    </div>
  );
}

export function AnalyzingStep() {
  const { t } = useTranslations();
  return (
    <LoadingStep
      message={t('enrichment.loading.analyzingTitle')}
      submessage={t('enrichment.loading.analyzingDescription')}
    />
  );
}

export function GeneratingStep() {
  const { t } = useTranslations();
  return (
    <LoadingStep
      message={t('enrichment.loading.generatingTitle')}
      submessage={t('enrichment.loading.generatingDescription')}
    />
  );
}

export function ApplyingStep() {
  const { t } = useTranslations();
  return (
    <LoadingStep
      message={t('enrichment.loading.applyingTitle')}
      submessage={t('enrichment.loading.applyingDescription')}
    />
  );
}

interface CompleteStepProps {
  onClose: () => void;
  updatedCount?: number;
}

export function CompleteStep({ onClose, updatedCount }: CompleteStepProps) {
  const { t } = useTranslations();
  const hasUpdatedCount = updatedCount !== undefined;
  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-6">
      <div className="relative">
        <CheckCircle2 className="h-16 w-16 text-[var(--at-success)]" />
      </div>
      <div className="text-center">
        <p className="text-2xl font-semibold text-[var(--foreground)]">
          {t('enrichment.complete.title')}
        </p>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">
          {hasUpdatedCount
            ? updatedCount === 1
              ? t('enrichment.complete.updatedCountSingular', { count: updatedCount })
              : t('enrichment.complete.updatedCountPlural', { count: updatedCount })
            : t('enrichment.complete.updatedFallback')}
        </p>
      </div>
      <Button onClick={onClose} className="mt-4 gap-2">
        <Sparkles className="h-4 w-4" />
        {t('enrichment.complete.doneButton')}
      </Button>
    </div>
  );
}

interface NoImprovementsStepProps {
  onClose: () => void;
  summary?: string;
}

export function NoImprovementsStep({ onClose, summary }: NoImprovementsStepProps) {
  const { t } = useTranslations();
  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-6">
      <div className="relative">
        <CheckCircle2 className="h-16 w-16 text-[var(--at-success)]" />
      </div>
      <div className="max-w-md text-center">
        <p className="text-2xl font-semibold text-[var(--foreground)]">
          {t('enrichment.noImprovements.title')}
        </p>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">
          {summary || t('enrichment.noImprovements.defaultDescription')}
        </p>
      </div>
      <Button onClick={onClose} className="mt-4 gap-2">
        <Sparkles className="h-4 w-4" />
        {t('common.close')}
      </Button>
    </div>
  );
}

interface ErrorStepProps {
  error: string;
  onRetry: () => void;
  onClose: () => void;
}

export function ErrorStep({ error, onRetry, onClose }: ErrorStepProps) {
  const { t } = useTranslations();
  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-6">
      <div className="relative">
        <AlertCircle className="h-16 w-16 text-[var(--destructive)]" />
      </div>
      <div className="max-w-md text-center">
        <p className="text-xl font-semibold text-[var(--foreground)]">
          {t('enrichment.error.title')}
        </p>
        <p className="mt-2 rounded-[var(--radius-at-md)] border border-[var(--destructive)]/40 bg-[var(--destructive)]/8 p-3 text-sm text-[var(--destructive)]">
          {error}
        </p>
      </div>
      <div className="mt-4 flex gap-3">
        <Button variant="outline" onClick={onClose}>
          {t('common.cancel')}
        </Button>
        <Button onClick={onRetry}>{t('common.retry')}</Button>
      </div>
    </div>
  );
}
