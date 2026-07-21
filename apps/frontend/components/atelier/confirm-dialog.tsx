'use client';

import * as React from 'react';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import CheckCircle from 'lucide-react/dist/esm/icons/circle-check';
import HelpCircle from 'lucide-react/dist/esm/icons/circle-help';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from './dialog';
import { Button } from './button';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';

/**
 * Atelier Confirm Dialog - token-based modal for confirming user actions.
 * Semantic variants map to the Atelier Button variants and success/warning/
 * destructive tokens. Replaces the legacy Swiss confirm dialog; same props API.
 */
export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  errorMessage?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmDisabled?: boolean;
  variant?: 'danger' | 'warning' | 'success' | 'default';
  closeOnConfirm?: boolean;
  onConfirm: () => void;
  onCancel?: () => void;
  showCancelButton?: boolean;
}

const VARIANTS = {
  danger: {
    Icon: AlertTriangle,
    iconClass: 'bg-[var(--destructive)]/12 text-[var(--destructive)]',
    button: 'destructive' as const,
  },
  warning: {
    Icon: AlertTriangle,
    iconClass: 'bg-[var(--at-warning)]/15 text-[var(--at-warning)]',
    button: 'warning' as const,
  },
  success: {
    Icon: CheckCircle,
    iconClass: 'bg-[var(--at-success)]/15 text-[var(--at-success)]',
    button: 'success' as const,
  },
  default: {
    Icon: HelpCircle,
    iconClass: 'bg-[var(--primary)]/12 text-[var(--primary)]',
    button: 'primary' as const,
  },
};

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  onOpenChange,
  title,
  description,
  errorMessage,
  confirmLabel,
  cancelLabel,
  confirmDisabled = false,
  variant = 'default',
  closeOnConfirm = true,
  onConfirm,
  onCancel,
  showCancelButton = true,
}) => {
  const { t } = useTranslations();
  const finalConfirmLabel = confirmLabel ?? t('common.confirm');
  const finalCancelLabel = cancelLabel ?? t('common.cancel');
  const { Icon, iconClass, button } = VARIANTS[variant];

  const handleConfirm = () => {
    if (confirmDisabled) return;
    onConfirm();
    if (closeOnConfirm) onOpenChange(false);
  };

  const handleCancel = () => {
    onCancel?.();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <span
              className={cn(
                'flex h-11 w-11 shrink-0 items-center justify-center rounded-full',
                iconClass
              )}
            >
              <Icon className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <DialogTitle>{title}</DialogTitle>
              <DialogDescription className="mt-1.5 max-h-60 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere]">
                {description}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {errorMessage && (
          <div className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-[var(--radius-at-md)] border border-[var(--destructive)]/40 bg-[var(--destructive)]/8 p-3 text-xs text-[var(--destructive)] [overflow-wrap:anywhere]">
            {errorMessage}
          </div>
        )}

        <DialogFooter>
          {showCancelButton && (
            <Button variant="outline" onClick={handleCancel}>
              {finalCancelLabel}
            </Button>
          )}
          <Button variant={button} onClick={handleConfirm} disabled={confirmDisabled}>
            {finalConfirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
