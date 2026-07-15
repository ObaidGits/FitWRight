'use client';

import { useEffect, useRef } from 'react';
import { XIcon, Sparkles } from 'lucide-react';
import { useEnrichmentWizard } from '@/hooks/use-enrichment-wizard';
import { useTranslations } from '@/lib/i18n';
import {
  AnalyzingStep,
  GeneratingStep,
  ApplyingStep,
  CompleteStep,
  NoImprovementsStep,
  ErrorStep,
} from './loading-steps';
import { QuestionStep } from './question-step';
import { PreviewStep } from './preview-step';

interface EnrichmentModalProps {
  resumeId: string;
  isOpen: boolean;
  onClose: () => void;
  onComplete: () => void;
}

export function EnrichmentModal({ resumeId, isOpen, onClose, onComplete }: EnrichmentModalProps) {
  const { t } = useTranslations();
  const dialogRef = useRef<HTMLDialogElement>(null);

  const {
    state,
    startAnalysis,
    setAnswer,
    nextQuestion,
    prevQuestion,
    generateEnhancements,
    applyChanges,
    reset,
    retry,
    currentQuestion,
    currentItem,
    isLastQuestion,
    isFirstQuestion,
    totalQuestions,
  } = useEnrichmentWizard(resumeId);

  // Handle dialog open/close
  useEffect(() => {
    if (isOpen) {
      dialogRef.current?.showModal();
      document.body.style.overflow = 'hidden';
      // Start analysis when modal opens
      if (state.step === 'idle') {
        startAnalysis();
      }
    } else {
      dialogRef.current?.close();
      document.body.style.overflow = 'auto';
    }

    const dialog = dialogRef.current;
    return () => {
      document.body.style.overflow = 'auto';
      if (dialog?.open) {
        dialog.close();
      }
    };
  }, [isOpen, state.step, startAnalysis]);

  // Handle close
  const handleClose = () => {
    reset();
    onClose();
  };

  // Handle complete
  const handleComplete = () => {
    reset();
    onComplete();
  };

  // Handle backdrop click
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === dialogRef.current) {
      // Only allow closing in certain states
      if (['idle', 'complete', 'error', 'no-improvements'].includes(state.step)) {
        handleClose();
      }
    }
  };

  // Handle ESC key
  const handleCancel = (e: React.SyntheticEvent<HTMLDialogElement, Event>) => {
    // Prevent closing during loading states
    if (['analyzing', 'generating', 'applying'].includes(state.step)) {
      e.preventDefault();
    } else {
      handleClose();
    }
  };

  // Handle finish questions - generate enhancements
  const handleFinishQuestions = () => {
    generateEnhancements();
  };

  if (!isOpen) return null;

  return (
    <dialog
      ref={dialogRef}
      className="fixed inset-0 z-50 w-full h-full p-0 m-0 max-w-none max-h-none bg-transparent border-none"
      onClick={handleBackdropClick}
      onCancel={handleCancel}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" />

      {/* Modal container - 80% viewport with padding */}
      <div className="absolute inset-0 flex items-center justify-center p-5 sm:p-10">
        <div className="relative flex h-full w-full max-w-[1200px] flex-col overflow-hidden rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-at-e3)]">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--secondary)]/40 px-6 py-4">
            <div className="flex items-center gap-3">
              <Sparkles className="h-5 w-5 text-[var(--primary)]" />
              <h1 className="text-lg font-semibold text-[var(--foreground)]">
                {t('enrichment.title')}
              </h1>
            </div>
            {/* Only show close button in non-loading states */}
            {!['analyzing', 'generating', 'applying'].includes(state.step) && (
              <button
                onClick={handleClose}
                className="rounded-[var(--radius-at-sm)] p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
              >
                <XIcon className="h-5 w-5" />
                <span className="sr-only">{t('common.close')}</span>
              </button>
            )}
          </div>

          {/* Content */}
          <div className="flex-1 overflow-hidden p-6">{renderStep()}</div>
        </div>
      </div>
    </dialog>
  );

  function renderStep() {
    switch (state.step) {
      case 'idle':
      case 'analyzing':
        return <AnalyzingStep />;

      case 'questions':
        if (!currentQuestion) {
          return <AnalyzingStep />;
        }
        return (
          <QuestionStep
            question={currentQuestion}
            item={currentItem}
            answer={state.answers[currentQuestion.question_id] || ''}
            questionNumber={state.currentQuestionIndex + 1}
            totalQuestions={totalQuestions}
            onAnswer={(answer) => setAnswer(currentQuestion.question_id, answer)}
            onNext={nextQuestion}
            onPrev={prevQuestion}
            onFinish={handleFinishQuestions}
            isFirst={isFirstQuestion}
            isLast={isLastQuestion}
          />
        );

      case 'generating':
        return <GeneratingStep />;

      case 'preview':
        return (
          <PreviewStep enhancements={state.preview} onApply={applyChanges} onCancel={handleClose} />
        );

      case 'applying':
        return <ApplyingStep />;

      case 'complete':
        return <CompleteStep onClose={handleComplete} updatedCount={state.preview.length} />;

      case 'no-improvements':
        return (
          <NoImprovementsStep onClose={handleClose} summary={state.analysisSummary || undefined} />
        );

      case 'error':
        return (
          <ErrorStep
            error={state.error || t('enrichment.error.unexpected')}
            onRetry={retry}
            onClose={handleClose}
          />
        );

      default:
        return null;
    }
  }
}
