/**
 * Read-back verification (auto-apply-brain Phase 0, R1.2).
 *
 * setValue/setSelectValue must report failure when the DOM does not actually
 * hold the intended value after the fill dispatch - the signature of a
 * React/Vue controlled input reverting the assignment on its next render, or a
 * site that silently reformats what was typed.
 */
import { describe, expect, it } from 'vitest';
import { readsBack, setValue } from '@/lib/dom';

describe('readsBack', () => {
  it('matches when the element genuinely holds the intended value', () => {
    const input = document.createElement('input');
    input.value = 'jane@example.com';
    expect(readsBack(input, 'jane@example.com')).toBe(true);
  });

  it('trims both sides before comparing, so surrounding whitespace is not a false mismatch', () => {
    const input = document.createElement('input');
    input.value = ' jane@example.com ';
    expect(readsBack(input, 'jane@example.com')).toBe(true);
  });

  it('fails when a framework reverted the value after the fill', () => {
    const input = document.createElement('input');
    // Simulate what a controlled input looks like the instant after a
    // same-tick revert: the DOM no longer holds what was written.
    input.value = '';
    expect(readsBack(input, 'jane@example.com')).toBe(false);
  });

  it('fails when the site reformatted the value rather than accepting it verbatim', () => {
    const input = document.createElement('input');
    input.value = '+1 (555) 000-1111'; // site added its own formatting
    expect(readsBack(input, '5550001111')).toBe(false);
  });
});

describe('setValue read-back integration', () => {
  it('a plain uncontrolled input reads back true', () => {
    const input = document.createElement('input');
    document.body.appendChild(input);
    expect(setValue(input, 'Ada Lovelace')).toBe(true);
    expect(input.value).toBe('Ada Lovelace');
    input.remove();
  });

  it('reports false when a change listener reverts the value synchronously', () => {
    // The closest jsdom can get to a controlled-input revert: a listener that
    // undoes the write before setValue's own read-back runs.
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.addEventListener('input', () => {
      input.value = '';
    });
    expect(setValue(input, 'Ada Lovelace')).toBe(false);
    input.remove();
  });
});
