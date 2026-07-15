import { describe, it, expect, vi } from 'vitest';
import {
  SseDecoder,
  StreamController,
  type SseEvent,
  type StreamTransport,
} from '@/lib/resilience/stream-client';

describe('SseDecoder', () => {
  it('parses token/done events split across chunks', () => {
    const d = new SseDecoder();
    let events: SseEvent[] = [];
    events = events.concat(d.push('event: token\nda'));
    expect(events).toEqual([]); // incomplete
    events = events.concat(d.push('ta: {"text":"Hi"}\n\nevent: done\n'));
    events = events.concat(d.push('data: {"cancelled":false,"text":"Hi"}\n\n'));
    expect(events[0]).toEqual({ event: 'token', data: { text: 'Hi' } });
    expect(events[1]).toEqual({ event: 'done', data: { cancelled: false, text: 'Hi' } });
  });

  it('handles CRLF separators and raw (non-JSON) data', () => {
    const d = new SseDecoder();
    const events = d.push('event: heartbeat\r\ndata: ping\r\n\r\n');
    expect(events[0].event).toBe('heartbeat');
    expect(events[0].data).toBe('ping');
  });
});

function transportFrom(
  events: SseEvent[],
  overrides: Partial<StreamTransport> = {}
): StreamTransport {
  return {
    async *open() {
      for (const e of events) yield e;
    },
    cancel: vi.fn(async () => {}),
    fallback: vi.fn(async () => 'FALLBACK TEXT'),
    ...overrides,
  };
}

describe('StreamController', () => {
  it('accumulates tokens and completes on done', async () => {
    const tokens: string[] = [];
    const transport = transportFrom([
      { event: 'heartbeat', data: {} },
      { event: 'token', data: { text: 'Hello ' } },
      { event: 'token', data: { text: 'world' } },
      {
        event: 'done',
        data: { cancelled: false, text: 'Hello world', usage: { total_tokens: 3 } },
      },
    ]);
    const ctrl = new StreamController(transport, { onToken: (_full, d) => tokens.push(d) });
    const text = await ctrl.run();
    expect(text).toBe('Hello world');
    expect(tokens).toEqual(['Hello ', 'world']);
    expect(ctrl.getStatus()).toBe('done');
  });

  it('falls back to non-stream on a terminal error event, surfacing fallback text', async () => {
    const transport = transportFrom([
      { event: 'token', data: { text: 'partial' } },
      { event: 'error', data: { message: 'boom', text: 'partial' } },
    ]);
    const ctrl = new StreamController(transport);
    const text = await ctrl.run();
    expect(transport.fallback).toHaveBeenCalled();
    expect(text).toBe('FALLBACK TEXT');
    expect(ctrl.getStatus()).toBe('done');
  });

  it('falls back when the stream throws (e.g. network drop)', async () => {
    const transport: StreamTransport = {
      async *open() {
        throw new Error('network');
      },
      cancel: vi.fn(async () => {}),
      fallback: vi.fn(async () => 'RECOVERED'),
    };
    const ctrl = new StreamController(transport);
    const text = await ctrl.run();
    expect(text).toBe('RECOVERED');
  });

  it('surfaces an error when fallback also fails', async () => {
    const onError = vi.fn();
    const transport = transportFrom([{ event: 'error', data: { message: 'boom' } }], {
      fallback: vi.fn(async () => {
        throw new Error('fallback down');
      }),
    });
    const ctrl = new StreamController(transport, { onError });
    await ctrl.run();
    expect(ctrl.getStatus()).toBe('error');
    expect(onError).toHaveBeenCalled();
  });

  it('cancel aborts and signals the server; no fallback on cancel', async () => {
    let resolveHang: () => void = () => {};
    const transport: StreamTransport = {
      async *open(signal) {
        yield { event: 'token', data: { text: 'so far' } };
        // Hang until aborted.
        await new Promise<void>((resolve) => {
          resolveHang = resolve;
          signal.addEventListener('abort', () => resolve());
        });
        yield { event: 'done', data: { cancelled: true, text: 'so far' } };
      },
      cancel: vi.fn(async () => {}),
      fallback: vi.fn(async () => 'SHOULD NOT RUN'),
    };
    const ctrl = new StreamController(transport);
    const runPromise = ctrl.run();
    await Promise.resolve();
    await ctrl.cancel();
    resolveHang();
    const text = await runPromise;
    expect(transport.cancel).toHaveBeenCalled();
    expect(transport.fallback).not.toHaveBeenCalled();
    expect(ctrl.getStatus()).toBe('cancelled');
    expect(text).toBe('so far');
  });
});
