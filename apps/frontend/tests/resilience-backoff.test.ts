import { describe, it, expect } from 'vitest';
import { fullJitterBackoff, CircuitBreaker } from '@/lib/resilience/backoff';

describe('fullJitterBackoff', () => {
  it('never exceeds the exponential ceiling and is bounded by cap', () => {
    // random() = 1 gives the max jittered value (floor of ceiling).
    expect(
      fullJitterBackoff(0, { baseMs: 500, capMs: 30000, random: () => 0.999999 })
    ).toBeLessThanOrEqual(500);
    expect(
      fullJitterBackoff(1, { baseMs: 500, capMs: 30000, random: () => 0.999999 })
    ).toBeLessThanOrEqual(1000);
    expect(
      fullJitterBackoff(3, { baseMs: 500, capMs: 30000, random: () => 0.999999 })
    ).toBeLessThanOrEqual(4000);
    // Cap enforced.
    expect(
      fullJitterBackoff(20, { baseMs: 500, capMs: 30000, random: () => 0.999999 })
    ).toBeLessThanOrEqual(30000);
  });

  it('is zero when random() is zero (full jitter allows immediate retry)', () => {
    expect(fullJitterBackoff(5, { baseMs: 500, capMs: 30000, random: () => 0 })).toBe(0);
  });
});

describe('CircuitBreaker', () => {
  it('trips open after the failure threshold and blocks attempts', () => {
    const t = 0;
    const b = new CircuitBreaker({ failureThreshold: 3, cooldownMs: 1000, now: () => t });
    expect(b.canAttempt()).toBe(true);
    b.recordFailure();
    b.recordFailure();
    expect(b.canAttempt()).toBe(true);
    b.recordFailure(); // 3rd → open
    expect(b.getState()).toBe('open');
    expect(b.canAttempt()).toBe(false);
  });

  it('moves open→half-open after cooldown and closes on a successful probe', () => {
    let t = 0;
    const b = new CircuitBreaker({ failureThreshold: 2, cooldownMs: 1000, now: () => t });
    b.recordFailure();
    b.recordFailure();
    expect(b.canAttempt()).toBe(false);
    t = 1000;
    expect(b.getState()).toBe('half-open');
    expect(b.canAttempt()).toBe(true);
    b.recordSuccess();
    expect(b.getState()).toBe('closed');
  });

  it('re-opens if the half-open probe fails', () => {
    let t = 0;
    const b = new CircuitBreaker({ failureThreshold: 2, cooldownMs: 1000, now: () => t });
    b.recordFailure();
    b.recordFailure();
    t = 1000;
    expect(b.getState()).toBe('half-open');
    b.recordFailure(); // failed probe → back to open
    expect(b.getState()).toBe('open');
    expect(b.canAttempt()).toBe(false);
  });

  it('resets consecutive failures on success', () => {
    const b = new CircuitBreaker({ failureThreshold: 3, cooldownMs: 1000 });
    b.recordFailure();
    b.recordFailure();
    b.recordSuccess();
    b.recordFailure();
    b.recordFailure();
    expect(b.canAttempt()).toBe(true); // only 2 since reset
  });
});
