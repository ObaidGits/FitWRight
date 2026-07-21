/**
 * Streaming AI client (P4 R1, Property 3).
 *
 * Two pieces, both transport-injected so they unit-test without a browser:
 * - {@link SseDecoder} - a pure incremental parser turning a byte/text SSE
 *   stream into `{ event, data }` records.
 * - {@link StreamController} - orchestrates a generation: accumulates `token`
 *   deltas into an `aria-live` preview, exposes cancel (aborts the request +
 *   signals the server), and on a terminal `error` (or unsupported/failed
 *   stream) transparently falls back to the non-stream path, surfacing any
 *   partial text as a discardable preview.
 *
 * Streamed output is a **preview**; it is persisted only via the existing
 * explicit accept path - never here.
 */

export interface SseEvent {
  event: string;
  data: unknown;
}

/** Incremental SSE parser (handles chunk boundaries mid-event). */
export class SseDecoder {
  private buffer = '';

  /** Feed a chunk of text; returns any complete events parsed so far. */
  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    let sep: number;
    // Events are separated by a blank line (\n\n). Handle \r\n too.
    while ((sep = this.indexOfSeparator(this.buffer)) !== -1) {
      const raw = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep).replace(/^(\r?\n){1,2}/, '');
      const parsed = this.parseBlock(raw);
      if (parsed) events.push(parsed);
    }
    return events;
  }

  private indexOfSeparator(s: string): number {
    const a = s.indexOf('\n\n');
    const b = s.indexOf('\r\n\r\n');
    if (a === -1) return b;
    if (b === -1) return a;
    return Math.min(a, b);
  }

  private parseBlock(block: string): SseEvent | null {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return null;
    const dataStr = dataLines.join('\n');
    let data: unknown = dataStr;
    try {
      data = JSON.parse(dataStr);
    } catch {
      /* leave as raw string */
    }
    return { event, data };
  }
}

export type StreamStatus = 'idle' | 'streaming' | 'done' | 'cancelled' | 'error' | 'fallback';

export interface StreamTransport {
  /** Open the SSE stream, yielding decoded events. Throw to trigger fallback. */
  open(signal: AbortSignal): AsyncIterable<SseEvent>;
  /** Signal server-side cancellation (POST .../cancel). */
  cancel(): Promise<void>;
  /** Non-stream fallback generation; resolves the full text. */
  fallback(): Promise<string>;
}

export interface StreamControllerCallbacks {
  onToken?: (fullText: string, delta: string) => void;
  onStatus?: (status: StreamStatus) => void;
  onDone?: (text: string, meta: { cancelled: boolean; usage?: unknown }) => void;
  onError?: (message: string) => void;
}

export class StreamController {
  private text = '';
  private status: StreamStatus = 'idle';
  private abort = new AbortController();
  private cancelled = false;

  constructor(
    private transport: StreamTransport,
    private cb: StreamControllerCallbacks = {}
  ) {}

  getText(): string {
    return this.text;
  }
  getStatus(): StreamStatus {
    return this.status;
  }

  private setStatus(status: StreamStatus): void {
    this.status = status;
    this.cb.onStatus?.(status);
  }

  /** Run the stream to completion (or fallback). Resolves the final text. */
  async run(): Promise<string> {
    this.setStatus('streaming');
    let usage: unknown;
    let terminalError: string | null = null;
    try {
      for await (const ev of this.transport.open(this.abort.signal)) {
        if (ev.event === 'token') {
          const delta = (ev.data as { text?: string })?.text ?? '';
          if (delta) {
            this.text += delta;
            this.cb.onToken?.(this.text, delta);
          }
        } else if (ev.event === 'heartbeat') {
          // liveness only
        } else if (ev.event === 'done') {
          const d = ev.data as { cancelled?: boolean; text?: string; usage?: unknown };
          if (typeof d?.text === 'string' && d.text.length >= this.text.length) {
            this.text = d.text;
          }
          usage = d?.usage;
          this.cancelled = Boolean(d?.cancelled);
          this.setStatus(this.cancelled ? 'cancelled' : 'done');
          this.cb.onDone?.(this.text, { cancelled: this.cancelled, usage });
          return this.text;
        } else if (ev.event === 'error') {
          const d = ev.data as { text?: string; message?: string };
          if (typeof d?.text === 'string' && d.text.length > this.text.length) {
            this.text = d.text;
          }
          terminalError = d?.message ?? 'stream_error';
          break;
        }
      }
    } catch (e) {
      if (this.cancelled) {
        this.setStatus('cancelled');
        this.cb.onDone?.(this.text, { cancelled: true });
        return this.text;
      }
      terminalError = e instanceof Error ? e.message : 'stream_error';
    }

    // If we got here without a `done`, the stream ended early or errored.
    if (this.cancelled) {
      this.setStatus('cancelled');
      this.cb.onDone?.(this.text, { cancelled: true });
      return this.text;
    }

    // Transparent fallback to the non-stream path (R1.3). Partial streamed text
    // remains as a discardable preview until fallback returns.
    this.setStatus('fallback');
    try {
      const full = await this.transport.fallback();
      this.text = full;
      this.setStatus('done');
      this.cb.onDone?.(this.text, { cancelled: false });
      return this.text;
    } catch (e) {
      this.setStatus('error');
      this.cb.onError?.(terminalError ?? (e instanceof Error ? e.message : 'generation_failed'));
      return this.text;
    }
  }

  /** Cancel the in-flight generation (R1.2): abort locally + signal the server. */
  async cancel(): Promise<void> {
    this.cancelled = true;
    this.abort.abort();
    try {
      await this.transport.cancel();
    } catch {
      /* best-effort; the abort already stopped local consumption */
    }
  }
}
