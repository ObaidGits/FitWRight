'use client';

/**
 * Shared AI-progress primitives (Loading Experience audit - P0).
 *
 * Two pure client-side hooks that back the {@link AiProgress} timeline:
 *
 * - `useDeterministicStages` advances through an ordered stage list on a
 *   *decelerating* timer for flows that expose no real progress channel. It is
 *   HONEST: it never marks the final stage complete until the caller flips
 *   `done`, and it never shows a fabricated percentage. When the work finishes
 *   it jumps straight to complete.
 *
 * - `useRotatingMessages` cycles reassurance microcopy on a single interval,
 *   paused under `prefers-reduced-motion`.
 *
 * Both are timer-only (no polling, no API calls) and clean up on unmount.
 */
import * as React from 'react';

/** True when the user asked the OS to minimize motion. SSR-safe. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener?.('change', update);
    return () => mq.removeEventListener?.('change', update);
  }, []);
  return reduced;
}

export interface DeterministicStagesResult {
  /** Index of the currently-active stage (0-based). */
  activeIndex: number;
  /** True once `done` was set and the timeline has fully completed. */
  complete: boolean;
  /** True if the timeline has been running longer than expected (escalation). */
  overdue: boolean;
}

export interface DeterministicStagesOptions {
  /** Flip to true when the real work resolves - completes the timeline honestly. */
  done: boolean;
  /** Whether the flow is currently running (false pauses/resets to idle). */
  active: boolean;
  /** Base per-stage dwell time in ms (default 1200). Later stages dwell longer. */
  baseMs?: number;
  /** Total time after which `overdue` flips true (default 20s). */
  overdueMs?: number;
}

/**
 * Advance through `count` stages on a decelerating timer while `active`.
 *
 * The final stage is a HOLD: the timer stops one short of the last stage and
 * only lands on it - and marks `complete` - once `done` is true. This guarantees
 * the UI never claims completion before the promise resolves.
 */
export function useDeterministicStages(
  count: number,
  { done, active, baseMs = 1200, overdueMs = 20_000 }: DeterministicStagesOptions
): DeterministicStagesResult {
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [complete, setComplete] = React.useState(false);
  const [overdue, setOverdue] = React.useState(false);

  // Reset whenever a fresh run starts.
  React.useEffect(() => {
    if (active && !done) {
      setActiveIndex(0);
      setComplete(false);
      setOverdue(false);
    }
  }, [active, done]);

  // Decelerating advance, holding one before the last stage until `done`.
  React.useEffect(() => {
    if (!active || done || count <= 0) return;
    const holdAt = Math.max(0, count - 1); // never auto-reach the final stage
    if (activeIndex >= holdAt) return;
    // Later stages dwell progressively longer so early feedback is snappy and
    // the tail doesn't race ahead of slow backends.
    const delay = baseMs + activeIndex * Math.round(baseMs * 0.6);
    const timer = window.setTimeout(() => setActiveIndex((i) => Math.min(i + 1, holdAt)), delay);
    return () => window.clearTimeout(timer);
  }, [active, done, count, activeIndex, baseMs]);

  // Overdue escalation.
  React.useEffect(() => {
    if (!active || done) return;
    const timer = window.setTimeout(() => setOverdue(true), overdueMs);
    return () => window.clearTimeout(timer);
  }, [active, done, overdueMs]);

  // Complete honestly when the real work resolves.
  React.useEffect(() => {
    if (done && active) {
      setActiveIndex(Math.max(0, count - 1));
      setComplete(true);
      setOverdue(false);
    }
  }, [done, active, count]);

  return { activeIndex, complete, overdue };
}

/**
 * Cycle `messages` on a fixed interval while `active`. Paused (holds the first
 * message) under reduced-motion or when there's ≤1 message.
 */
export function useRotatingMessages(
  messages: string[],
  { active, intervalMs = 2600 }: { active: boolean; intervalMs?: number }
): string {
  const reduced = usePrefersReducedMotion();
  const [index, setIndex] = React.useState(0);

  React.useEffect(() => {
    if (!active) setIndex(0);
  }, [active]);

  React.useEffect(() => {
    if (!active || reduced || messages.length <= 1) return;
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % messages.length), intervalMs);
    return () => window.clearInterval(timer);
  }, [active, reduced, messages.length, intervalMs]);

  return messages[Math.min(index, Math.max(0, messages.length - 1))] ?? '';
}
