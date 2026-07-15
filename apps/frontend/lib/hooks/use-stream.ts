'use client';

/**
 * useStream — progressive streaming AI generation (P4 R1.1, R1.3, R6.5).
 *
 * Wraps {@link StreamController} + the fetch-based transport. Accumulates tokens
 * into `text` (rendered in an `aria-live="polite"` region by the consumer),
 * exposes `cancel()`, and transparently falls back to the non-stream path on
 * error. Streamed output is a **preview** — the caller persists only via the
 * existing accept/confirm path.
 */
import * as React from 'react';
import { StreamController, type StreamStatus } from '@/lib/resilience/stream-client';
import { buildResumeStreamTransport, type StreamKind } from '@/lib/api/resume';

export interface UseStreamResult {
  text: string;
  status: StreamStatus;
  isStreaming: boolean;
  error: string | null;
  /** Start a generation; resolves with the final (or fallback) text. */
  start: (kind: StreamKind) => Promise<string>;
  cancel: () => void;
  reset: () => void;
}

function newRequestId(): string {
  return typeof crypto?.randomUUID === 'function'
    ? crypto.randomUUID()
    : `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useStream(
  resumeId: string,
  opts?: { streamingEnabled?: boolean }
): UseStreamResult {
  const [text, setText] = React.useState('');
  const [status, setStatus] = React.useState<StreamStatus>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const controllerRef = React.useRef<StreamController | null>(null);

  const reset = React.useCallback(() => {
    setText('');
    setStatus('idle');
    setError(null);
  }, []);

  const start = React.useCallback(
    async (kind: StreamKind): Promise<string> => {
      setText('');
      setError(null);
      const requestId = newRequestId();
      const transport = buildResumeStreamTransport(resumeId, kind, requestId);

      // When streaming is flag-disabled, skip the SSE round-trip and go straight
      // to the non-stream fallback (same result, one fewer request).
      if (opts?.streamingEnabled === false) {
        setStatus('fallback');
        try {
          const full = await transport.fallback();
          setText(full);
          setStatus('done');
          return full;
        } catch (e) {
          setStatus('error');
          setError(e instanceof Error ? e.message : 'generation_failed');
          return '';
        }
      }

      const controller = new StreamController(transport, {
        onToken: (full) => setText(full),
        onStatus: (s) => setStatus(s),
        onError: (m) => setError(m),
      });
      controllerRef.current = controller;
      return controller.run();
    },
    [resumeId, opts?.streamingEnabled]
  );

  const cancel = React.useCallback(() => {
    void controllerRef.current?.cancel();
  }, []);

  React.useEffect(() => {
    return () => {
      void controllerRef.current?.cancel();
    };
  }, []);

  return {
    text,
    status,
    isStreaming: status === 'streaming' || status === 'fallback',
    error,
    start,
    cancel,
    reset,
  };
}
