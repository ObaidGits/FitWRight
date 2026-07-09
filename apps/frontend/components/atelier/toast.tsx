'use client';

/**
 * Lightweight toast system (Atelier) — transient notifications (Req 33.1).
 * Dependency-free (no external toast lib); accessible via aria-live region.
 */
import * as React from 'react';
import X from 'lucide-react/dist/esm/icons/x';
import CheckCircle from 'lucide-react/dist/esm/icons/circle-check';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import Info from 'lucide-react/dist/esm/icons/info';
import { cn } from '@/lib/utils';

export type ToastVariant = 'success' | 'error' | 'info';
export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
}

interface ToastContextValue {
  toast: (
    t: Omit<Toast, 'id' | 'duration' | 'variant'> & { variant?: ToastVariant; duration?: number }
  ) => void;
  dismiss: (id: string) => void;
}

const ToastContext = React.createContext<ToastContextValue | undefined>(undefined);

const ICONS = { success: CheckCircle, error: AlertTriangle, info: Info } as const;
const ACCENT = {
  success: 'text-[var(--at-success)]',
  error: 'text-[var(--destructive)]',
  info: 'text-[var(--primary)]',
} as const;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<Toast[]>([]);

  const dismiss = React.useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = React.useCallback<ToastContextValue['toast']>(
    ({ variant = 'info', duration = 5000, ...rest }) => {
      const id = Math.random().toString(36).slice(2);
      setToasts((prev) => [...prev, { id, variant, duration, ...rest }]);
      if (duration > 0) window.setTimeout(() => dismiss(id), duration);
    },
    [dismiss]
  );

  const value = React.useMemo(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2"
        role="region"
        aria-label="Notifications"
      >
        {toasts.map((t) => {
          const Icon = ICONS[t.variant];
          return (
            <div
              key={t.id}
              role="status"
              aria-live="polite"
              className={cn(
                'pointer-events-auto flex items-start gap-3 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-3.5 shadow-[var(--shadow-at-e2)]',
                'data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom-2'
              )}
            >
              <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', ACCENT[t.variant])} />
              <div className="flex-1 space-y-0.5">
                <p className="text-sm font-medium text-[var(--foreground)]">{t.title}</p>
                {t.description && (
                  <p className="text-xs text-[var(--muted-foreground)]">{t.description}</p>
                )}
              </div>
              <button
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss notification"
                className="shrink-0 rounded-[var(--radius-at-sm)] p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = React.useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
}
