'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/atelier/button';
import { Textarea } from '@/components/atelier/input';
import { ChevronLeft, ChevronRight, Briefcase, FolderKanban } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { EnrichmentQuestion, EnrichmentItem } from '@/lib/api/enrichment';
import { useTranslations } from '@/lib/i18n';

interface QuestionStepProps {
  question: EnrichmentQuestion;
  item: EnrichmentItem | undefined;
  answer: string;
  questionNumber: number;
  totalQuestions: number;
  onAnswer: (answer: string) => void;
  onNext: () => void;
  onPrev: () => void;
  onFinish: () => void;
  isFirst: boolean;
  isLast: boolean;
}

export function QuestionStep({
  question,
  item,
  answer,
  questionNumber,
  totalQuestions,
  onAnswer,
  onNext,
  onPrev,
  onFinish,
  isFirst,
  isLast,
}: QuestionStepProps) {
  const { t } = useTranslations();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [localAnswer, setLocalAnswer] = useState(answer);

  // Sync local answer with prop
  useEffect(() => {
    setLocalAnswer(answer);
  }, [answer, question.question_id]);

  // Auto-focus textarea when question changes
  useEffect(() => {
    textareaRef.current?.focus();
  }, [question.question_id]);

  const handleChange = (value: string) => {
    setLocalAnswer(value);
    onAnswer(value);
  };

  const handleContinue = useCallback(() => {
    if (isLast) {
      onFinish();
    } else {
      onNext();
    }
  }, [isLast, onFinish, onNext]);

  // Handle keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Enter without shift = next/finish (only if textarea not focused or ctrl/cmd held)
      if (e.key === 'Enter' && !e.shiftKey && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        handleContinue();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleContinue]);

  return (
    <div className="flex h-full min-h-[500px] flex-col">
      {/* Progress indicator */}
      <div className="mb-8 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm text-[var(--muted-foreground)]">
            {t('enrichment.questionProgress', { current: questionNumber, total: totalQuestions })}
          </span>
        </div>
        <div className="flex gap-1">
          {Array.from({ length: totalQuestions }).map((_, i) => (
            <div
              key={i}
              className={cn(
                'h-1.5 w-6 rounded-full transition-colors',
                i <= questionNumber - 1 ? 'bg-[var(--primary)]' : 'bg-[var(--secondary)]'
              )}
            />
          ))}
        </div>
      </div>

      {/* Item context badge */}
      {item && (
        <div className="mb-6">
          <div className="inline-flex items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--secondary)]/40 px-3 py-1.5 text-sm">
            {item.item_type === 'experience' ? (
              <Briefcase className="h-4 w-4 text-[var(--muted-foreground)]" />
            ) : (
              <FolderKanban className="h-4 w-4 text-[var(--muted-foreground)]" />
            )}
            <span className="text-[var(--muted-foreground)]">
              {item.item_type === 'experience'
                ? t('enrichment.itemType.experience')
                : t('enrichment.itemType.project')}
              :
            </span>
            <span className="font-semibold text-[var(--foreground)]">{item.title}</span>
            {item.subtitle && (
              <span className="text-[var(--muted-foreground)]">@ {item.subtitle}</span>
            )}
          </div>
        </div>
      )}

      {/* Question */}
      <div className="flex-1">
        <h2 className="mb-6 text-2xl font-semibold leading-tight text-[var(--foreground)]">
          {question.question}
        </h2>

        <Textarea
          ref={textareaRef}
          value={localAnswer}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={question.placeholder}
          className="min-h-[180px] resize-none text-base"
        />

        <p className="mt-2 text-xs text-[var(--muted-foreground)]">
          {t('enrichment.shortcutHint')}
        </p>
      </div>

      {/* Navigation */}
      <div className="mt-6 flex items-center justify-between border-t border-[var(--border)] pt-6">
        <Button variant="outline" onClick={onPrev} disabled={isFirst} className="gap-2">
          <ChevronLeft className="h-4 w-4" />
          {t('common.back')}
        </Button>

        <Button onClick={handleContinue} className="gap-2">
          {isLast ? (
            <>
              {t('common.finish')}
              <ChevronRight className="h-4 w-4" />
            </>
          ) : (
            <>
              {t('common.continue')}
              <ChevronRight className="h-4 w-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
