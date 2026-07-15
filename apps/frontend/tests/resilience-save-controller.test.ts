import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  SaveController,
  type SaveControllerOptions,
  type SaveOutcome,
  type SaveContext,
} from '@/lib/resilience/save-controller';

interface Payload {
  summary: string;
}

function makeController(
  save: (p: Payload, ctx: SaveContext) => Promise<SaveOutcome>,
  overrides: Partial<SaveControllerOptions<Payload>> = {}
) {
  const persistDraft = vi.fn(async () => {});
  const onStatus = vi.fn();
  const onConflict = vi.fn();
  const onSaved = vi.fn();
  let key = 0;
  const controller = new SaveController<Payload>({
    save,
    persistDraft,
    isOnline: () => true,
    newIdempotencyKey: () => `key-${++key}`,
    onStatus,
    onConflict,
    onSaved,
    debounceMs: 1000,
    backoffBaseMs: 100,
    backoffCapMs: 2000,
    breakerThreshold: 3,
    breakerCooldownMs: 5000,
    ...overrides,
  });
  return { controller, persistDraft, onStatus, onConflict, onSaved };
}

describe('SaveController', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('debounces and saves the latest content once', async () => {
    const save = vi.fn(
      async (_p: Payload, _ctx: SaveContext): Promise<SaveOutcome> => ({ type: 'ok', version: 2 })
    );
    const { controller, persistDraft, onSaved } = makeController(save);
    controller.setBaseVersion(1);

    controller.update({ summary: 'a' });
    controller.update({ summary: 'ab' });
    controller.update({ summary: 'abc' });
    expect(save).not.toHaveBeenCalled(); // still debouncing

    await vi.advanceTimersByTimeAsync(1000);
    expect(persistDraft).toHaveBeenCalled(); // durable draft before network
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toEqual({ summary: 'abc' }); // latest wins
    expect(save.mock.calls[0][1].baseVersion).toBe(1);
    expect(onSaved).toHaveBeenCalledWith(2, undefined);
    expect(controller.getState().status).toBe('saved');
    expect(controller.getState().baseVersion).toBe(2);
  });

  it('writes the durable draft before the network attempt', async () => {
    const order: string[] = [];
    const save = vi.fn(async (): Promise<SaveOutcome> => {
      order.push('save');
      return { type: 'ok', version: 2 };
    });
    const { controller, persistDraft } = makeController(save);
    persistDraft.mockImplementation(async () => {
      order.push('draft');
    });
    controller.update({ summary: 'x' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(order).toEqual(['draft', 'save']);
  });

  it('coalesces a single in-flight request plus one trailing save', async () => {
    let resolveFirst: (o: SaveOutcome) => void = () => {};
    const save = vi
      .fn<(p: Payload, ctx: SaveContext) => Promise<SaveOutcome>>()
      .mockImplementationOnce(() => new Promise<SaveOutcome>((r) => (resolveFirst = r)))
      .mockImplementation(async () => ({ type: 'ok', version: 3 }) as SaveOutcome);
    const { controller } = makeController(save);
    controller.setBaseVersion(1);

    controller.update({ summary: 'first' });
    await vi.advanceTimersByTimeAsync(1000); // starts first save (in-flight)
    expect(save).toHaveBeenCalledTimes(1);

    // Edits arrive while in-flight → coalesced into a single trailing save.
    controller.update({ summary: 'second' });
    controller.update({ summary: 'third' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(save).toHaveBeenCalledTimes(1); // still just the in-flight one

    resolveFirst({ type: 'ok', version: 2 });
    await vi.advanceTimersByTimeAsync(0);
    // Trailing save fires exactly once with the newest content.
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ summary: 'third' });
  });

  it('treats an identical-content save as a no-op (R4.2)', async () => {
    const save = vi.fn(
      async (_p: Payload, _c: SaveContext): Promise<SaveOutcome> => ({ type: 'ok', version: 2 })
    );
    const { controller } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'same' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(save).toHaveBeenCalledTimes(1);
    // Re-updating with identical content must not hit the network again.
    controller.update({ summary: 'same' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(save).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe('saved');
  });

  it('noteExternalSave reconciles so a later identical flush is a no-op', async () => {
    const save = vi.fn(
      async (_p: Payload, _c: SaveContext): Promise<SaveOutcome> => ({ type: 'ok', version: 9 })
    );
    const { controller } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'queued offline' });
    // Simulate the outbox drain persisting this exact content externally.
    controller.noteExternalSave(2, { summary: 'queued offline' });
    await vi.advanceTimersByTimeAsync(1000);
    // The pending flush sees identical content already saved → no network call.
    expect(save).not.toHaveBeenCalled();
    expect(controller.getState().baseVersion).toBe(2);
  });

  it('routes a 409 into the conflict flow and does not blind-retry', async () => {
    const info = { yourBaseVersion: 1, currentVersion: 5, currentData: { summary: 'server' } };
    const save = vi.fn(async (): Promise<SaveOutcome> => ({ type: 'conflict', info }));
    const { controller, onConflict } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'mine' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(onConflict).toHaveBeenCalledWith(info);
    expect(controller.getState().status).toBe('conflict');

    // Further debounce ticks must NOT auto-save over an unresolved conflict.
    await vi.advanceTimersByTimeAsync(5000);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it('reuses the idempotency key across retries of the same content', async () => {
    const keys: string[] = [];
    let attempt = 0;
    const save = vi.fn(async (_p, ctx: SaveContext): Promise<SaveOutcome> => {
      keys.push(ctx.idempotencyKey);
      attempt += 1;
      return attempt < 2 ? { type: 'transient' } : { type: 'ok', version: 2 };
    });
    const { controller } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'x' });
    await vi.advanceTimersByTimeAsync(1000); // first attempt: transient
    await vi.advanceTimersByTimeAsync(2000); // backoff retry: ok
    expect(save).toHaveBeenCalledTimes(2);
    expect(keys[0]).toBe(keys[1]); // same key so the server dedupes
  });

  it('retries transient failures with backoff then succeeds', async () => {
    let attempt = 0;
    const save = vi.fn(async (): Promise<SaveOutcome> => {
      attempt += 1;
      return attempt < 3 ? { type: 'transient' } : { type: 'ok', version: 2 };
    });
    const { controller, onStatus } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'x' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(controller.getState().status).toBe('retrying');
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);
    expect(controller.getState().status).toBe('saved');
    expect(onStatus).toHaveBeenCalledWith('retrying', expect.anything());
  });

  it('trips the breaker after repeated failures (stops hammering)', async () => {
    const save = vi.fn(async (): Promise<SaveOutcome> => ({ type: 'transient' }));
    const { controller } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'x' });
    // Drive through several retry cycles.
    await vi.advanceTimersByTimeAsync(1000);
    for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(2000);
    const callsAfterOpen = save.mock.calls.length;
    // After the breaker opens, additional time should not add many calls.
    await vi.advanceTimersByTimeAsync(2000);
    expect(save.mock.calls.length).toBeLessThanOrEqual(callsAfterOpen + 1);
    expect(controller.getState().status).toBe('retrying');
  });

  it('goes offline and keeps the draft when not reachable', async () => {
    const save = vi.fn(async (): Promise<SaveOutcome> => ({ type: 'ok', version: 2 }));
    const online = false;
    const { controller, persistDraft } = makeController(save, { isOnline: () => online });
    controller.setBaseVersion(1);
    controller.update({ summary: 'x' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(persistDraft).toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
    expect(controller.getState().status).toBe('offline');
  });

  it('resolveConflict("keep mine") re-bases and writes a fresh save', async () => {
    const save = vi
      .fn()
      .mockResolvedValueOnce({
        type: 'conflict',
        info: { yourBaseVersion: 1, currentVersion: 5, currentData: {} },
      } as SaveOutcome)
      .mockResolvedValue({ type: 'ok', version: 6 } as SaveOutcome);
    const { controller } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'mine' });
    await vi.advanceTimersByTimeAsync(1000);
    expect(controller.getState().status).toBe('conflict');

    await controller.resolveConflict({ summary: 'mine rebased' }, 5);
    await vi.advanceTimersByTimeAsync(1000);
    expect(save).toHaveBeenLastCalledWith(
      { summary: 'mine rebased' },
      expect.objectContaining({ baseVersion: 5 })
    );
    expect(controller.getState().baseVersion).toBe(6);
  });

  it('resolveConflict(null) takes latest without a write', async () => {
    const save = vi.fn().mockResolvedValueOnce({
      type: 'conflict',
      info: { yourBaseVersion: 1, currentVersion: 5, currentData: {} },
    } as SaveOutcome);
    const { controller } = makeController(save);
    controller.setBaseVersion(1);
    controller.update({ summary: 'mine' });
    await vi.advanceTimersByTimeAsync(1000);
    await controller.resolveConflict(null, 5);
    expect(controller.getState().status).toBe('saved');
    expect(controller.getState().baseVersion).toBe(5);
    expect(save).toHaveBeenCalledTimes(1); // only the initial conflicting attempt
  });
});
