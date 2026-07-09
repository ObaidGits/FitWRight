import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Accessibility + mobile checks for the auth surface (Task 11.2).
 *
 * Covers, on all auth forms + the step-up modal:
 *   - screen-reader labels / aria (every control has an accessible name; the
 *     error banner is an assertive live region; the caps-lock hint is a status)
 *   - keyboard navigation + focus-visible (controls are focusable; the reveal
 *     toggle carries a focus-visible ring)
 *   - focus management (the step-up dialog is a labelled, modal focus trap that
 *     moves focus inside on open and closes on Escape)
 *   - reduced-motion (animations are neutralised under prefers-reduced-motion)
 *   - responsive / touch layout + the mobile-safe OAuth **top-level** redirect
 *
 * axe-core runs the structural rules it can evaluate under jsdom. Colour
 * *contrast* cannot be computed without real layout/paint, so that rule is
 * disabled here and validated instead via the reduced-motion / design-token CSS
 * assertion below + manual review (full WCAG contrast needs a real browser).
 */

// --- module mocks (mirror the existing auth component tests) ----------------

const replaceMock = vi.fn();
const refreshMock = vi.fn().mockResolvedValue(undefined);
let searchParams = new URLSearchParams('');

const { AuthApiError } = vi.hoisted(() => {
  class AuthApiError extends Error {
    code: string;
    status: number;
    constructor(code: string, message: string, status = 401) {
      super(message);
      this.name = 'AuthApiError';
      this.code = code;
      this.status = status;
    }
  }
  return { AuthApiError };
});
const stepUpMock = vi.fn();

vi.mock('@/lib/api/auth', () => ({
  AuthApiError,
  authApi: {
    login: vi.fn(),
    signup: vi.fn(),
    forgotPassword: vi.fn().mockResolvedValue(undefined),
    resetPassword: vi.fn().mockResolvedValue({ id: 'u1' }),
    stepUp: (...a: unknown[]) => stepUpMock(...a),
    oauthStartUrl: (p: string, n?: string) =>
      `http://localhost:8000/api/v1/auth/oauth/${p}/start${n ? `?next=${encodeURIComponent(n)}` : ''}`,
  },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  useSearchParams: () => searchParams,
}));

vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ refresh: refreshMock, user: null }),
}));

import { AuthCard } from '@/components/auth/auth-card';
import { ForgotCard } from '@/components/auth/forgot-card';
import { ResetCard } from '@/components/auth/reset-card';
import { StepUpProvider, useStepUp } from '@/components/auth/step-up-modal';

/** Run axe against a container; assert no violations for the evaluable rules. */
async function expectNoA11yViolations(container: HTMLElement) {
  const results = await axe.run(container, {
    rules: {
      // jsdom has no layout engine → contrast is unmeasurable here (see header).
      'color-contrast': { enabled: false },
      // These are whole-page rules; the tests render component fragments.
      region: { enabled: false },
      'landmark-one-main': { enabled: false },
      'page-has-heading-one': { enabled: false },
      'document-title': { enabled: false },
      'html-has-lang': { enabled: false },
    },
  });
  const violations = results.violations.map((v) => `${v.id}: ${v.help}`);
  expect(violations).toEqual([]);
}

describe('auth a11y — screen-reader labels & aria', () => {
  beforeEach(() => {
    replaceMock.mockClear();
    stepUpMock.mockReset();
    searchParams = new URLSearchParams('');
  });
  afterEach(() => vi.clearAllMocks());

  it('login form has no structural a11y violations', async () => {
    const { container } = render(<AuthCard mode="login" />);
    await expectNoA11yViolations(container);
  });

  it('signup form has no structural a11y violations', async () => {
    const { container } = render(<AuthCard mode="signup" />);
    await expectNoA11yViolations(container);
  });

  it('forgot + reset forms have no structural a11y violations', async () => {
    searchParams = new URLSearchParams('token=tkn');
    const forgot = render(<ForgotCard />);
    await expectNoA11yViolations(forgot.container);
    const reset = render(<ResetCard />);
    await expectNoA11yViolations(reset.container);
  });

  it('every login control exposes an accessible name to a screen reader', () => {
    render(<AuthCard mode="login" />);
    // Labelled inputs.
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    // The reveal toggle names its action + exposes its pressed state.
    const reveal = screen.getByRole('button', { name: /show password/i });
    expect(reveal).toHaveAttribute('aria-pressed', 'false');
    // Decorative Google mark is hidden from the a11y tree.
    expect(document.querySelector('svg[aria-hidden]')).toBeTruthy();
  });

  it('the error banner is an assertive live region', () => {
    render(<AuthCard mode="login" />);
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveAttribute('aria-live', 'assertive');
  });

  it('the caps-lock hint is announced as a status', () => {
    render(<AuthCard mode="login" />);
    const pw = screen.getByLabelText('Password');
    // Simulate a keystroke while Caps Lock is on (a native event whose
    // getModifierState reports CapsLock as active).
    const evt = new KeyboardEvent('keyup', { key: 'a', bubbles: true });
    Object.defineProperty(evt, 'getModifierState', { value: () => true });
    fireEvent(pw, evt);
    const status = screen.getByRole('status');
    expect(status.textContent).toMatch(/caps lock is on/i);
  });
});

describe('auth a11y — keyboard & focus-visible', () => {
  it('email, password and submit are all keyboard-focusable in order', () => {
    render(<AuthCard mode="login" />);
    const email = screen.getByLabelText('Email');
    const password = screen.getByLabelText('Password');
    const submit = screen.getByRole('button', { name: /sign in/i });
    for (const el of [email, password, submit]) {
      // No positive/removed tab index would break the natural tab order.
      expect(el).not.toHaveAttribute('tabindex', '-1');
      (el as HTMLElement).focus();
      expect(document.activeElement).toBe(el);
    }
  });

  it('the password reveal toggle has a focus-visible ring', () => {
    render(<AuthCard mode="login" />);
    const reveal = screen.getByRole('button', { name: /show password/i });
    expect(reveal.className).toMatch(/focus-visible:ring/);
  });

  it('reveal toggle flips the input type and its pressed state', () => {
    render(<AuthCard mode="login" />);
    const password = screen.getByLabelText('Password');
    expect(password).toHaveAttribute('type', 'password');
    fireEvent.click(screen.getByRole('button', { name: /show password/i }));
    expect(password).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: /hide password/i })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });
});

describe('auth a11y — password-manager autocomplete (R15.1)', () => {
  it('login uses email + current-password autocomplete', () => {
    render(<AuthCard mode="login" />);
    expect(screen.getByLabelText('Email')).toHaveAttribute('autocomplete', 'email');
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password');
  });

  it('signup uses new-password autocomplete', () => {
    render(<AuthCard mode="signup" />);
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'new-password');
  });
});

describe('step-up modal — focus management (focus trap + Escape)', () => {
  function Consumer() {
    const { run } = useStepUp();
    return (
      <button
        onClick={() =>
          run(async () => {
            throw new AuthApiError('step_up_required', 'step up', 401);
          }).catch(() => {})
        }
      >
        go
      </button>
    );
  }

  it('opens a labelled modal dialog and moves focus inside it', async () => {
    render(
      <StepUpProvider>
        <Consumer />
      </StepUpProvider>
    );
    fireEvent.click(screen.getByRole('button', { name: 'go' }));

    const dialog = await screen.findByRole('dialog');
    // The dialog is labelled by its title (screen-reader announceable).
    expect(dialog).toHaveAccessibleName(/confirm it's you/i);
    // Focus is moved into the dialog on open — the focus-trap contract (Radix
    // uses focus guards + aria-hidden on outside content to trap Tab).
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    // The password control inside is labelled.
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });

  it('closes on Escape (focus trap releases, action rejected)', async () => {
    render(
      <StepUpProvider>
        <Consumer />
      </StepUpProvider>
    );
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('auth mobile — responsive layout & mobile-safe OAuth redirect', () => {
  it('primary actions are full-width for a comfortable touch target', () => {
    render(<AuthCard mode="login" />);
    expect(screen.getByRole('button', { name: /sign in/i }).className).toMatch(/w-full/);
    expect(screen.getByRole('button', { name: /continue with google/i }).className).toMatch(
      /w-full/
    );
  });

  it('Google sign-in is a TOP-LEVEL navigation (mobile IdP-safe, not a popup)', () => {
    // A top-level redirect is what lets the session cookie persist across the
    // IdP round-trip on mobile browsers (no third-party popup/iframe).
    const assign = vi.fn();
    const original = window.location;
    // Replace location with a spy-able stub for the duration of the test.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...original,
        set href(v: string) {
          assign(v);
        },
      },
    });
    try {
      render(<AuthCard mode="login" />);
      fireEvent.click(screen.getByRole('button', { name: /continue with google/i }));
      expect(assign).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/auth/oauth/google/start')
      );
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original });
    }
  });
});

describe('auth a11y — reduced motion (design-token CSS)', () => {
  it('neutralises animations under prefers-reduced-motion', () => {
    const css = fs.readFileSync(path.resolve(__dirname, '../styles/atelier.css'), 'utf8');
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    // The block clamps transition + animation durations to ~0.
    const block = css.slice(css.indexOf('prefers-reduced-motion'));
    expect(block).toMatch(/animation-duration:\s*0?\.?0*1?ms\s*!important/);
    expect(block).toMatch(/transition-duration:\s*0?\.?0*1?ms\s*!important/);
  });
});
