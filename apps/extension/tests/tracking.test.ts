/**
 * Submission detection.
 *
 * The bug these pin: the delayed fallback reported success unconditionally, so a
 * form that rejected the application for a missing field still marked the job
 * applied six seconds later. That silently suppressed the duplicate guard's advice
 * to re-apply and counted the job as sent in the reply-rate view - corrupting the
 * one number that tells the user which resume works.
 *
 * So the valuable assertions here are the negative ones: a rejected submission must
 * NOT report, and a corrected retry must still be watched.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { watchForSubmission } from '@/content/tracking';

/** Advance past the 6-second fallback. */
function runFallback(): void {
  vi.advanceTimersByTime(6500);
}

/**
 * jsdom implements no layout, so every element reports `offsetParent === null` and
 * no client rects - which the production code correctly reads as "not visible".
 * Tests that need an element to count as on-screen have to say so.
 */
function makeVisible(el: Element): void {
  Object.defineProperty(el, 'offsetParent', { value: document.body, configurable: true });
  el.getClientRects = () => [{ width: 10, height: 10 }] as unknown as DOMRectList;
}

/** Let MutationObserver callbacks (microtasks) run under fake timers. */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

const APPLICATION_FORM = `
  <form id="app">
    <label for="name">Full name</label><input id="name" name="name" type="text" />
    <label for="email">Email</label><input id="email" name="email" type="email" />
    <label for="cv">Resume</label><input id="cv" name="cv" type="file" />
    <button type="submit">Submit application</button>
  </form>
`;

let teardown: (() => void) | undefined;

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = APPLICATION_FORM;
});

afterEach(() => {
  teardown?.();
  teardown = undefined;
  vi.useRealTimers();
});

describe('a submission that was accepted', () => {
  it('reports when a confirmation message appears', async () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    // The site swaps the form for a thank-you.
    document.body.innerHTML = '<div id="done"></div>';
    const done = document.getElementById('done') as HTMLElement;
    const message = document.createElement('p');
    message.textContent = 'Thank you for applying! Your application was submitted.';
    done.appendChild(message);
    await flush();

    expect(onSubmitted).toHaveBeenCalledTimes(1);
  });

  it('reports on the fallback when the form is gone and nothing objected', () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    // A silent success: the page navigated and the form is no longer present.
    document.body.innerHTML = '<main>Your applications</main>';
    runFallback();

    expect(onSubmitted).toHaveBeenCalledTimes(1);
  });
});

describe('a submission that was rejected', () => {
  it('does NOT report when a required field is still invalid', () => {
    const onSubmitted = vi.fn();
    document.body.innerHTML = `
      <form id="app">
        <input id="name" name="name" type="text" required />
        <input id="cv" name="cv" type="file" />
        <button type="submit">Submit application</button>
      </form>
    `;
    makeVisible(document.getElementById('name') as HTMLElement);
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    runFallback();

    // The form is still there with an empty required field: nothing was sent.
    expect(onSubmitted).not.toHaveBeenCalled();
  });

  it('does NOT report when the site shows a validation message', () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    const error = document.createElement('p');
    error.textContent = 'Please enter your phone number.';
    document.querySelector('form')?.appendChild(error);
    runFallback();

    expect(onSubmitted).not.toHaveBeenCalled();
  });

  it('does NOT report when a custom widget is marked invalid', () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    const field = document.getElementById('name') as HTMLElement;
    field.setAttribute('aria-invalid', 'true');
    makeVisible(field);
    runFallback();

    expect(onSubmitted).not.toHaveBeenCalled();
  });

  it('re-arms after a rejection, so a silent success on retry still reports', () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    // First attempt is rejected with a validation message.
    const form = document.querySelector('form');
    form?.dispatchEvent(new Event('submit', { bubbles: true }));
    const error = document.createElement('p');
    error.textContent = 'This field is required.';
    form?.appendChild(error);
    runFallback();
    expect(onSubmitted).not.toHaveBeenCalled();

    // The user corrects it and submits again, and this time the site accepts it
    // *silently* - no confirmation text, just a navigation. Before the fix the
    // one-shot guard had been spent on the failed attempt, so nothing could arm a
    // second fallback and this success was never recorded.
    error.remove();
    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    document.body.innerHTML = '<main>Dashboard</main>';
    runFallback();

    expect(onSubmitted).toHaveBeenCalledTimes(1);
  });
});

describe('what counts as an attempt', () => {
  it('ignores a search box', () => {
    const onSubmitted = vi.fn();
    document.body.innerHTML = `
      <form id="search"><input type="search" name="q" /><button>Search</button></form>
    `;
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    runFallback();

    expect(onSubmitted).not.toHaveBeenCalled();
  });

  it('treats a click on an apply button as an attempt', () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    const button = document.querySelector('button') as HTMLButtonElement;
    button.click();
    // Form gone, nothing objected: accepted.
    document.body.innerHTML = '<main>Done</main>';
    runFallback();

    expect(onSubmitted).toHaveBeenCalledTimes(1);
  });

  it('reports only once', () => {
    const onSubmitted = vi.fn();
    teardown = watchForSubmission({ onSubmitted });

    document.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true }));
    document.body.innerHTML = '<main>Application submitted</main>';
    runFallback();
    runFallback();

    expect(onSubmitted).toHaveBeenCalledTimes(1);
  });
});
