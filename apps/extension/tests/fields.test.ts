/**
 * Field classification: which Profile value belongs in which form input.
 *
 * This is where a mistake is expensive rather than merely annoying. The rule
 * worth more than the rest of the extension's tests combined: **a specific
 * address field must not be swallowed by the generic location rule.** The rules
 * are first-match-wins, and a broad `location` pattern once matched "City",
 * "Address line 1", "State" and "Country" - which would have typed "Pune, India"
 * into all four boxes of every form the user opened.
 *
 * Driven through real DOM rather than bare strings, so the label-reading path is
 * exercised too: a rule that works on a string and fails on a `<label>` is not
 * working.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { classify } from '@/lib/fields';

/** Build a labelled input the way an ATS renders one, and classify it. */
function keyFor(label: string, attrs = ''): string | null {
  document.body.innerHTML = `
    <label for="f">${label}</label>
    <input id="f" ${attrs} />
  `;
  const el = document.getElementById('f') as HTMLInputElement;
  return classify(el);
}

describe('address fields stay distinct', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('does not collapse specific address parts into the generic location', () => {
    // The regression this pins: all four once resolved to `location`.
    expect(keyFor('City')).not.toBe('location');
    expect(keyFor('Address line 1')).not.toBe('location');
    expect(keyFor('State')).not.toBe('location');
    expect(keyFor('Country')).not.toBe('location');
  });

  it('maps each address part to its own field', () => {
    expect(keyFor('City')).toBe('city');
    expect(keyFor('Country')).toBe('country');
  });
});

describe('contact fields', () => {
  it('recognises the labels every form uses', () => {
    expect(keyFor('Email address *')).toBe('email');
    expect(keyFor('First name')).toBe('first_name');
    expect(keyFor('Last name')).toBe('last_name');
  });

  it('trusts the input type over the label', () => {
    // An input typed `email` is an email box whatever the label calls it.
    expect(keyFor('Your contact', 'type="email"')).toBe('email');
    expect(keyFor('Reach you on', 'type="tel"')).toBe('phone');
  });
});

describe('unknown labels', () => {
  it('resolve to nothing rather than to a guess', () => {
    expect(keyFor('Describe a time you disagreed with a manager')).toBeNull();
  });

  it('classify nothing when there is no label at all', () => {
    expect(keyFor('')).toBeNull();
    expect(keyFor('***')).toBeNull();
  });
});
