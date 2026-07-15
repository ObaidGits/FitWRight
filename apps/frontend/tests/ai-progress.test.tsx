import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, act, renderHook } from '@testing-library/react';

import { AiProgress } from '@/components/ai/ai-progress';
import { useDeterministicStages, useRotatingMessages } from '@/lib/hooks/use-ai-progress';

const STAGES = [
  { key: 'a', label: 'Reading your document' },
  { key: 'b', label: 'Understanding the layout' },
  { key: 'c', label: 'Building your resume' },
];

describe('useDeterministicStages', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('advances but HOLDS one before the last stage until done', () => {
    const { result, rerender } = renderHook(
      ({ done }) => useDeterministicStages(3, { done, active: true, baseMs: 1000 }),
      { initialProps: { done: false } }
    );
    expect(result.current.activeIndex).toBe(0);
    expect(result.current.complete).toBe(false);

    act(() => void vi.advanceTimersByTime(5000));
    // Never auto-reaches the final stage (holds at index 1 of 3).
    expect(result.current.activeIndex).toBe(1);
    expect(result.current.complete).toBe(false);

    // Real work resolves → jumps to final stage + complete.
    rerender({ done: true });
    expect(result.current.activeIndex).toBe(2);
    expect(result.current.complete).toBe(true);
  });

  it('flags overdue after the threshold', () => {
    const { result } = renderHook(() =>
      useDeterministicStages(3, { done: false, active: true, overdueMs: 3000 })
    );
    expect(result.current.overdue).toBe(false);
    act(() => void vi.advanceTimersByTime(3500));
    expect(result.current.overdue).toBe(true);
  });
});

describe('useRotatingMessages', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('cycles messages on the interval while active', () => {
    const msgs = ['one', 'two', 'three'];
    const { result } = renderHook(() =>
      useRotatingMessages(msgs, { active: true, intervalMs: 1000 })
    );
    expect(result.current).toBe('one');
    act(() => void vi.advanceTimersByTime(1000));
    expect(result.current).toBe('two');
    act(() => void vi.advanceTimersByTime(1000));
    expect(result.current).toBe('three');
    act(() => void vi.advanceTimersByTime(1000));
    expect(result.current).toBe('one'); // wraps
  });
});

describe('<AiProgress> live mode', () => {
  it('renders stage states from activeKey and announces via aria-live', () => {
    render(<AiProgress stages={STAGES} activeKey="b" doneKeys={['a']} estimate="Usually 5s." />);
    // All stage labels render.
    expect(screen.getByText('Reading your document')).toBeInTheDocument();
    expect(screen.getByText('Understanding the layout')).toBeInTheDocument();
    // The live-region announces the active stage.
    expect(screen.getByRole('status')).toHaveTextContent('Understanding the layout.');
    expect(screen.getByText('Usually 5s.')).toBeInTheDocument();
  });
});
