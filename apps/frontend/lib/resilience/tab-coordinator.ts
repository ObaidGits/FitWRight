/**
 * TabCoordinator — multi-tab leader election + save fan-out (P4 R7, Property 6).
 *
 * Among N tabs of the same account, exactly one is the **leader** that owns
 * autosave + outbox flushing for a resource; followers defer persistence to it,
 * preventing duplicate/racing saves and interleaved IndexedDB writes.
 *
 * Mechanism:
 * - **Web Locks** (`navigator.locks`) elect the leader: a tab requests an
 *   exclusive lock and holds it (leader) until it closes/crashes, at which point
 *   the OS releases the lock and a waiting tab acquires it — automatic
 *   re-election within a bounded time (R7.2). The lock, not message trust, is
 *   the source of authority (defends against forged BroadcastChannel messages).
 * - **BroadcastChannel** fans out `save` events (so followers refresh their base
 *   version and avoid a self-inflicted 409, R7.3) and `editing` announcements
 *   (so a tab can warn "open in another tab", R7.4).
 *
 * Both dependencies are injected so the election + fan-out logic unit-tests
 * without a real browser. When Web Locks are unavailable the coordinator
 * degrades to "this tab is leader" (single-tab assumption) rather than blocking.
 */

export interface LockGrant {
  release: () => void;
}

export interface LockManagerLike {
  /**
   * Acquire `name` exclusively; invoke `onAcquired` with a release handle. The
   * returned promise resolves when the lock is released. Mirrors the subset of
   * `navigator.locks` we use, adapted for injection/testing.
   */
  acquire(name: string, onAcquired: (grant: LockGrant) => Promise<void>): Promise<void>;
}

export interface ChannelLike {
  postMessage(message: unknown): void;
  close(): void;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

export interface CoordinatorMessage {
  type: 'save' | 'editing' | 'logout';
  senderId: string;
  resourceId?: string;
  version?: number;
  contentHash?: string;
}

export interface TabCoordinatorOptions {
  userId: string;
  locks?: LockManagerLike;
  createChannel?: (name: string) => ChannelLike;
  onLeadershipChange?: (isLeader: boolean) => void;
  onRemoteSave?: (resourceId: string, version: number, contentHash?: string) => void;
  onRemoteEditing?: (resourceId: string, senderId: string) => void;
  onRemoteLogout?: () => void;
}

/** Default Web Locks adapter (browser). */
function browserLockManager(): LockManagerLike | null {
  if (typeof navigator === 'undefined' || !('locks' in navigator)) return null;
  return {
    acquire(name, onAcquired) {
      // navigator.locks.request holds the lock for the lifetime of the callback
      // promise; we resolve it via the release handle.
      return (navigator as Navigator & { locks: LockManager }).locks.request(
        name,
        { mode: 'exclusive' },
        () =>
          new Promise<void>((resolve) => {
            onAcquired({ release: resolve }).catch(() => resolve());
          })
      );
    },
  };
}

function browserChannelFactory(name: string): ChannelLike | null {
  if (typeof BroadcastChannel === 'undefined') return null;
  return new BroadcastChannel(name) as unknown as ChannelLike;
}

let idCounter = 0;
function makeTabId(): string {
  idCounter += 1;
  const rand =
    typeof globalThis.crypto?.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `tab-${rand}-${idCounter}`;
}

export class TabCoordinator {
  readonly tabId = makeTabId();
  private leader = false;
  private channel: ChannelLike | null = null;
  private grant: LockGrant | null = null;
  private disposed = false;
  private readonly o: TabCoordinatorOptions;
  private readonly locks: LockManagerLike | null;

  constructor(opts: TabCoordinatorOptions) {
    this.o = opts;
    this.locks = opts.locks ?? browserLockManager();
  }

  isLeader(): boolean {
    return this.leader;
  }

  /** Begin leader election + open the coordination channel. */
  start(): void {
    if (this.disposed) return;
    const factory = this.o.createChannel ?? browserChannelFactory;
    this.channel = factory(`fitwright-coord:${this.o.userId}`);
    if (this.channel) {
      this.channel.onmessage = (ev) => this.handleMessage(ev.data as CoordinatorMessage);
    }

    if (!this.locks) {
      // No Web Locks → assume single tab; become leader immediately.
      this.setLeader(true);
      return;
    }
    // Request leadership; the promise resolves only when we release (dispose)
    // or the lock is lost. If another tab holds it we wait (follower) until it
    // releases, then become leader (re-election).
    void this.locks.acquire(`fitwright-leader:${this.o.userId}`, async (grant) => {
      this.grant = grant;
      this.setLeader(true);
      await new Promise<void>((resolve) => {
        this.releaseLeadership = resolve;
      });
    });
  }

  private releaseLeadership: (() => void) | null = null;

  private setLeader(value: boolean): void {
    if (this.leader === value) return;
    this.leader = value;
    this.o.onLeadershipChange?.(value);
  }

  private handleMessage(msg: CoordinatorMessage): void {
    if (!msg || msg.senderId === this.tabId) return; // ignore our own
    switch (msg.type) {
      case 'save':
        if (msg.resourceId && typeof msg.version === 'number') {
          this.o.onRemoteSave?.(msg.resourceId, msg.version, msg.contentHash);
        }
        break;
      case 'editing':
        if (msg.resourceId) this.o.onRemoteEditing?.(msg.resourceId, msg.senderId);
        break;
      case 'logout':
        this.o.onRemoteLogout?.();
        break;
    }
  }

  /** Fan out a successful save so other tabs refresh their base version (R7.3). */
  broadcastSave(resourceId: string, version: number, contentHash?: string): void {
    this.channel?.postMessage({
      type: 'save',
      senderId: this.tabId,
      resourceId,
      version,
      contentHash,
    } satisfies CoordinatorMessage);
  }

  /** Announce that this tab is editing a resource (drives "open elsewhere", R7.4). */
  announceEditing(resourceId: string): void {
    this.channel?.postMessage({
      type: 'editing',
      senderId: this.tabId,
      resourceId,
    } satisfies CoordinatorMessage);
  }

  /** Broadcast a logout so other tabs clear local data (R8.2 different-user). */
  broadcastLogout(): void {
    this.channel?.postMessage({
      type: 'logout',
      senderId: this.tabId,
    } satisfies CoordinatorMessage);
  }

  dispose(): void {
    this.disposed = true;
    // Release the leadership lock so a waiting tab is elected (re-election).
    this.releaseLeadership?.();
    this.releaseLeadership = null;
    this.grant?.release();
    this.grant = null;
    if (this.channel) {
      this.channel.onmessage = null;
      this.channel.close();
      this.channel = null;
    }
    this.setLeader(false);
  }
}
