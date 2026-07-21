'use client';

/**
 * Global error boundary - the last line of defense. Catches render errors that
 * escape the root layout (which segment `error.tsx` boundaries cannot). Because
 * it replaces the whole document, it renders its own <html>/<body>. Kept
 * dependency-free and inline-styled so it works even if app CSS failed to load.
 */
import * as React from 'react';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    // Surface for observability; digest correlates with server logs.
    console.error('Global error boundary:', error);
  }, [error]);

  return (
    <html lang="en-US">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
          background: '#0b0b0f',
          color: '#e7e7ea',
          padding: '1.5rem',
        }}
      >
        <div style={{ maxWidth: 420, textAlign: 'center' }}>
          <h1 style={{ fontSize: '1.35rem', margin: '0 0 0.5rem' }}>Something went wrong</h1>
          <p style={{ opacity: 0.75, margin: '0 0 1.5rem', lineHeight: 1.5 }}>
            An unexpected error occurred. You can try again, or head back to your workspace.
          </p>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
            <button
              onClick={() => reset()}
              style={{
                cursor: 'pointer',
                border: 'none',
                borderRadius: 10,
                padding: '0.6rem 1.1rem',
                fontSize: '0.9rem',
                fontWeight: 600,
                background: '#6d5efc',
                color: '#fff',
              }}
            >
              Try again
            </button>
            <a
              href="/home"
              style={{
                borderRadius: 10,
                padding: '0.6rem 1.1rem',
                fontSize: '0.9rem',
                fontWeight: 600,
                border: '1px solid rgba(255,255,255,0.2)',
                color: '#e7e7ea',
                textDecoration: 'none',
              }}
            >
              Back to home
            </a>
          </div>
        </div>
      </body>
    </html>
  );
}
