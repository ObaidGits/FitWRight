import { describe, it, expect, vi } from 'vitest';
import {
  TabCoordinator,
  type ChannelLike,
  type LockManagerLike,
  type LockGrant,
} from '@/lib/resilience/tab-coordinator';

/**
 * A single-holder exclusive lock shared across "tabs" in one test process,
 * mirroring `navigator.locks`: the lock is held for the callback's lifetime and
 * released when the callback promise resolves; waiters are served FIFO.
 */
function makeSharedLock(): LockManagerLike & { holders: number } {
  let busy = false;
  const waiters: Array<() => void> = [];
  const state = {
    holders: 0,
    async acquire(_name: string, onAcquired: (grant: LockGrant) => Promise<void>) {
      if (busy) {
        await new Promise<void>((res) => waiters.push(res));
      }
      busy = true;
      state.holders += 1;
      let released = false;
      const release = () => {
        if (released) return;
        released = true;
        state.holders -= 1;
        busy = false;
        const next = waiters.shift();
        if (next) next();
      };
      // Hold the lock until the callback promise resolves, then release.
      await onAcquired({ release });
      release();
    },
  };
  return state;
}

/** An in-process BroadcastChannel bus. */
function makeBus() {
  const channels: ChannelLike[] = [];
  return {
    create(): ChannelLike {
      const ch: ChannelLike = {
        onmessage: null,
        postMessage(message: unknown) {
          for (const other of channels) {
            if (other !== ch && other.onmessage) other.onmessage({ data: message });
          }
        },
        close() {
          const i = channels.indexOf(ch);
          if (i >= 0) channels.splice(i, 1);
        },
      };
      channels.push(ch);
      return ch;
    },
  };
}

describe('TabCoordinator', () => {
  it('elects exactly one leader among tabs and re-elects on leader dispose', async () => {
    const lock = makeSharedLock();
    const bus = makeBus();
    const a = new TabCoordinator({ userId: 'u1', locks: lock, createChannel: () => bus.create() });
    const b = new TabCoordinator({ userId: 'u1', locks: lock, createChannel: () => bus.create() });
    a.start();
    b.start();
    await Promise.resolve();
    await Promise.resolve();
    // Exactly one leader.
    expect(lock.holders).toBe(1);
    expect([a.isLeader(), b.isLeader()].filter(Boolean).length).toBe(1);

    const firstLeader = a.isLeader() ? a : b;
    const follower = a.isLeader() ? b : a;
    expect(follower.isLeader()).toBe(false);

    // Leader closes → follower is re-elected.
    firstLeader.dispose();
    await Promise.resolve();
    await Promise.resolve();
    expect(follower.isLeader()).toBe(true);
    expect(lock.holders).toBe(1);
    follower.dispose();
  });

  it('degrades to leader when Web Locks are unavailable', () => {
    const bus = makeBus();
    const solo = new TabCoordinator({
      userId: 'u1',
      locks: undefined,
      createChannel: () => bus.create(),
    });
    // No injected lock + no navigator.locks in jsdom → immediate leader.
    solo.start();
    expect(solo.isLeader()).toBe(true);
    solo.dispose();
  });

  it('fans out save events to other tabs (avoids self-inflicted conflict)', async () => {
    const bus = makeBus();
    const lock = makeSharedLock();
    const onRemoteSave = vi.fn();
    const a = new TabCoordinator({ userId: 'u1', locks: lock, createChannel: () => bus.create() });
    const b = new TabCoordinator({
      userId: 'u1',
      locks: lock,
      createChannel: () => bus.create(),
      onRemoteSave,
    });
    a.start();
    b.start();
    a.broadcastSave('r1', 7, 'hash7');
    expect(onRemoteSave).toHaveBeenCalledWith('r1', 7, 'hash7');
    a.dispose();
    b.dispose();
  });

  it('does not deliver a tab its own messages', () => {
    const bus = makeBus();
    const onRemoteSave = vi.fn();
    const a = new TabCoordinator({
      userId: 'u1',
      locks: undefined,
      createChannel: () => bus.create(),
      onRemoteSave,
    });
    a.start();
    a.broadcastSave('r1', 3);
    expect(onRemoteSave).not.toHaveBeenCalled();
    a.dispose();
  });

  it('relays editing announcements and logout', () => {
    const bus = makeBus();
    const onRemoteEditing = vi.fn();
    const onRemoteLogout = vi.fn();
    const a = new TabCoordinator({
      userId: 'u1',
      locks: undefined,
      createChannel: () => bus.create(),
    });
    const b = new TabCoordinator({
      userId: 'u1',
      locks: undefined,
      createChannel: () => bus.create(),
      onRemoteEditing,
      onRemoteLogout,
    });
    a.start();
    b.start();
    a.announceEditing('r1');
    expect(onRemoteEditing).toHaveBeenCalledWith('r1', a.tabId);
    a.broadcastLogout();
    expect(onRemoteLogout).toHaveBeenCalled();
    a.dispose();
    b.dispose();
  });
});
