/**
 * Retry backoff + client circuit breaker (P4 R4.4, Property 4).
 *
 * Pure, dependency-injected primitives so a single flaky-backend brownout does
 * not turn thousands of clients into a retry storm:
 *
 * - {@link fullJitterBackoff} — exponential backoff with *full jitter*
 *   (AWS-style): `random(0, min(cap, base * 2^attempt))`. Full jitter (not
 *   equal jitter) maximises decorrelation between clients so they don't retry
 *   in lock-step.
 * - {@link CircuitBreaker} — a closed→open→half-open state machine that stops
 *   hammering an endpoint that keeps failing and probes for recovery. While
 *   open, the caller must not attempt the network (the durable local draft
 *   still protects the work).
 */

export interface BackoffOptions {
  /** Base delay in ms (the first retry's ceiling before jitter). */
  baseMs: number;
  /** Absolute ceiling in ms regardless of attempt count. */
  capMs: number;
  /** Injectable RNG for deterministic tests (defaults to Math.random). */
  random?: () => number;
}

/**
 * Full-jitter exponential backoff delay for a zero-based `attempt` number.
 * `attempt=0` is the delay before the first retry.
 */
export function fullJitterBackoff(attempt: number, opts: BackoffOptions): number {
  const random = opts.random ?? Math.random;
  const exp = Math.min(opts.capMs, opts.baseMs * 2 ** Math.max(0, attempt));
  return Math.floor(random() * exp);
}

export type BreakerState = 'closed' | 'open' | 'half-open';

export interface CircuitBreakerOptions {
  /** Consecutive failures that trip the breaker from closed → open. */
  failureThreshold: number;
  /** Cooldown (ms) before an open breaker moves to half-open to probe. */
  cooldownMs: number;
  /** Injectable clock (ms) for deterministic tests. */
  now?: () => number;
}

/**
 * Client-side circuit breaker.
 *
 * Transitions:
 * - `closed` → `open` after `failureThreshold` consecutive failures.
 * - `open` → `half-open` once `cooldownMs` has elapsed (probed on `canAttempt`).
 * - `half-open` → `closed` on a success; `half-open` → `open` on a failure.
 *
 * `canAttempt()` is the single gate the SaveController/SyncController consults
 * before a network attempt.
 */
export class CircuitBreaker {
  private state: BreakerState = 'closed';
  private consecutiveFailures = 0;
  private openedAt = 0;
  private readonly failureThreshold: number;
  private readonly cooldownMs: number;
  private readonly now: () => number;

  constructor(opts: CircuitBreakerOptions) {
    this.failureThreshold = Math.max(1, opts.failureThreshold);
    this.cooldownMs = Math.max(0, opts.cooldownMs);
    this.now = opts.now ?? Date.now;
  }

  /** Current state, promoting open→half-open when the cooldown has elapsed. */
  getState(): BreakerState {
    if (this.state === 'open' && this.now() - this.openedAt >= this.cooldownMs) {
      this.state = 'half-open';
    }
    return this.state;
  }

  /** Whether a network attempt is currently permitted. */
  canAttempt(): boolean {
    return this.getState() !== 'open';
  }

  /** Record a successful attempt: resets failures and closes the breaker. */
  recordSuccess(): void {
    this.consecutiveFailures = 0;
    this.state = 'closed';
  }

  /**
   * Record a failed attempt. A failure while half-open (a failed probe) or
   * reaching the threshold while closed trips the breaker back open.
   */
  recordFailure(): void {
    // Ensure any pending open→half-open promotion is applied first.
    const state = this.getState();
    this.consecutiveFailures += 1;
    if (state === 'half-open' || this.consecutiveFailures >= this.failureThreshold) {
      this.trip();
    }
  }

  private trip(): void {
    this.state = 'open';
    this.openedAt = this.now();
  }
}
