/**
 * Backend reachability probe (P4 R2.6, R9.6).
 *
 * `navigator.onLine` is advisory only - it lies on captive portals and when the
 * backend is down but the LAN is up. The source of truth for "can we actually
 * reach the backend" is a short-timeout `GET /api/v1/health` probe. This
 * doubles as the free-tier keep-warm ping (design §Overview, ADR-15).
 */

export interface ReachabilityOptions {
  url?: string;
  timeoutMs?: number;
  fetchFn?: typeof fetch;
}

/** One-shot reachability probe. Resolves true only on a 2xx within the timeout. */
export async function probeReachability(opts: ReachabilityOptions = {}): Promise<boolean> {
  const url = opts.url ?? '/api/v1/health';
  const timeoutMs = opts.timeoutMs ?? 3000;
  const doFetch = opts.fetchFn ?? (typeof fetch !== 'undefined' ? fetch : undefined);
  if (!doFetch) return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await doFetch(url, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'omit',
    });
    return resp.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Probe the backend and return `{ reachable, apiVersion }`. `apiVersion` is the
 * `X-API-Version` header the server advertises (P4 R9.8) so the client can pin a
 * baseline and detect a deploy mid-session (version skew -> Safe-Mode).
 */
export async function probeApiVersion(
  opts: ReachabilityOptions = {}
): Promise<{ reachable: boolean; apiVersion: string | null }> {
  const url = opts.url ?? '/api/v1/health';
  const timeoutMs = opts.timeoutMs ?? 3000;
  const doFetch = opts.fetchFn ?? (typeof fetch !== 'undefined' ? fetch : undefined);
  if (!doFetch) return { reachable: false, apiVersion: null };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await doFetch(url, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'omit',
    });
    return { reachable: resp.ok, apiVersion: resp.headers.get('X-API-Version') };
  } catch {
    return { reachable: false, apiVersion: null };
  } finally {
    clearTimeout(timer);
  }
}

export type ReachabilityListener = (reachable: boolean) => void;

/**
 * Monitors backend reachability by combining `online`/`offline` browser events
 * with periodic real probes. The probe result - never `navigator.onLine` alone
 * - is the authoritative `isReachable()`.
 */
export class ReachabilityMonitor {
  private reachable = true;
  private listeners = new Set<ReachabilityListener>();
  private intervalHandle: ReturnType<typeof setInterval> | null = null;
  private readonly opts: ReachabilityOptions;
  private readonly intervalMs: number;

  constructor(opts: ReachabilityOptions & { intervalMs?: number } = {}) {
    this.opts = opts;
    this.intervalMs = opts.intervalMs ?? 20_000;
  }

  isReachable(): boolean {
    return this.reachable;
  }

  subscribe(listener: ReachabilityListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private set(value: boolean): void {
    if (this.reachable === value) return;
    this.reachable = value;
    for (const l of this.listeners) l(value);
  }

  /** Probe now and update state. Returns the fresh result. */
  async check(): Promise<boolean> {
    const ok = await probeReachability(this.opts);
    this.set(ok);
    return ok;
  }

  start(): void {
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this.onOnline);
      window.addEventListener('offline', this.onOffline);
    }
    void this.check();
    this.intervalHandle = setInterval(() => void this.check(), this.intervalMs);
  }

  private onOnline = () => {
    // Browser says online - verify with a real probe before trusting it (R2.6).
    void this.check();
  };
  private onOffline = () => {
    this.set(false);
  };

  stop(): void {
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this.onOnline);
      window.removeEventListener('offline', this.onOffline);
    }
    if (this.intervalHandle) clearInterval(this.intervalHandle);
    this.intervalHandle = null;
  }
}
