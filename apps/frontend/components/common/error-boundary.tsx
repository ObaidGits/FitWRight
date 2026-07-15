'use client';

import React, { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/atelier/button';
import { useTranslations } from '@/lib/i18n';

interface ErrorBoundaryStrings {
  title: string;
  description: string;
  tryAgain: string;
  reloadPage: string;
}

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  strings?: ErrorBoundaryStrings;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * Error Boundary component to catch React errors and display a fallback UI.
 * Prevents entire app from crashing when a component throws an error.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // Log error to console (could be sent to error tracking service)
    console.error('Error Boundary caught an error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    const strings: ErrorBoundaryStrings = this.props.strings ?? {
      title: 'Something Went Wrong',
      description: 'An unexpected error occurred. This has been logged for review.',
      tryAgain: 'Try Again',
      reloadPage: 'Reload Page',
    };

    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="flex min-h-[400px] flex-col items-center justify-center bg-[var(--background)] p-8">
          <div className="w-full max-w-md rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-8 shadow-[var(--shadow-at-e2)]">
            <div className="mb-4 flex items-center gap-3">
              <AlertTriangle className="h-8 w-8 text-[var(--destructive)]" />
              <h2 className="text-2xl font-semibold text-[var(--foreground)]">{strings.title}</h2>
            </div>

            <p className="mb-4 text-sm text-[var(--muted-foreground)]">{strings.description}</p>

            {process.env.NODE_ENV === 'development' && this.state.error && (
              <div className="mb-4 rounded-[var(--radius-at-md)] border border-[var(--destructive)]/40 bg-[var(--destructive)]/8 p-3">
                <p className="break-all font-mono text-xs text-[var(--destructive)]">
                  {this.state.error.message}
                </p>
              </div>
            )}

            <div className="flex gap-3">
              <Button onClick={this.handleReset} variant="outline" className="flex-1">
                {strings.tryAgain}
              </Button>
              <Button onClick={this.handleReload} className="flex-1">
                <RefreshCw className="mr-2 h-4 w-4" />
                {strings.reloadPage}
              </Button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * Higher-order component to wrap any component with an error boundary.
 */
export function withErrorBoundary<P extends object>(
  WrappedComponent: React.ComponentType<P>,
  fallback?: ReactNode
) {
  return function WithErrorBoundaryWrapper(props: P) {
    return (
      <ErrorBoundary fallback={fallback}>
        <WrappedComponent {...props} />
      </ErrorBoundary>
    );
  };
}

export function LocalizedErrorBoundary({
  children,
  fallback,
}: {
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { t } = useTranslations();
  const strings: ErrorBoundaryStrings = {
    title: t('errors.boundary.title'),
    description: t('errors.boundary.description'),
    tryAgain: t('errors.boundary.tryAgain'),
    reloadPage: t('errors.boundary.reloadPage'),
  };

  return (
    <ErrorBoundary fallback={fallback} strings={strings}>
      {children}
    </ErrorBoundary>
  );
}

export default ErrorBoundary;
