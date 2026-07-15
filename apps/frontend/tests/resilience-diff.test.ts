import { describe, it, expect } from 'vitest';
import { computeConflictDiff, fieldMerge } from '@/lib/resilience/diff';

describe('computeConflictDiff', () => {
  const base = { summary: 'base', name: 'Jane', skills: ['a'] };

  it('detects disjoint changes as mergeable', () => {
    const mine = { ...base, summary: 'mine' };
    const latest = { ...base, name: 'Janet' };
    const diff = computeConflictDiff(base, mine, latest);
    expect(diff.mineChanged.map((c) => c.field)).toEqual(['summary']);
    expect(diff.latestChanged.map((c) => c.field)).toEqual(['name']);
    expect(diff.overlapping).toEqual([]);
    expect(diff.mergeable).toBe(true);
  });

  it('detects overlapping changes as NOT mergeable', () => {
    const mine = { ...base, summary: 'mine' };
    const latest = { ...base, summary: 'theirs' };
    const diff = computeConflictDiff(base, mine, latest);
    expect(diff.overlapping).toEqual(['summary']);
    expect(diff.mergeable).toBe(false);
  });

  it('ignores key-order noise via stable stringify', () => {
    const mine = { name: 'Jane', summary: 'base', skills: ['a'] };
    const latest = { skills: ['a'], summary: 'base', name: 'Jane' };
    const diff = computeConflictDiff(base, mine, latest);
    expect(diff.mineChanged).toEqual([]);
    expect(diff.latestChanged).toEqual([]);
    expect(diff.mergeable).toBe(false); // no local changes → nothing to merge
  });

  it('treats nested object changes as a field change', () => {
    const b = { info: { city: 'NYC' } };
    const mine = { info: { city: 'LA' } };
    const latest = { info: { city: 'NYC' } };
    const diff = computeConflictDiff(b, mine, latest);
    expect(diff.mineChanged.map((c) => c.field)).toEqual(['info']);
    expect(diff.mergeable).toBe(true);
  });
});

describe('fieldMerge', () => {
  it('applies only the local-changed fields onto latest', () => {
    const latest = { summary: 'server', name: 'Janet', skills: ['a', 'b'] };
    const mineChanged = [{ field: 'summary', base: 'x', value: 'my summary' }];
    const merged = fieldMerge(latest, mineChanged);
    expect(merged).toEqual({ summary: 'my summary', name: 'Janet', skills: ['a', 'b'] });
  });
});
