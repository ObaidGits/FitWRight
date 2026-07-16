import { describe, expect, it } from 'vitest';
import LoginPage from '@/app/(auth)/login/page';
import SignupPage from '@/app/(auth)/signup/page';
import { AuthCard } from '@/components/auth/auth-card';

describe('auth pages server-resolve query state', () => {
  it('returns the login card directly instead of a null Suspense fallback', async () => {
    const element = await LoginPage({
      searchParams: Promise.resolve({ next: '/applications', error: 'oauth_failed' }),
    });
    expect(element.type).toBe(AuthCard);
    expect(element.props).toMatchObject({
      mode: 'login',
      initialNext: '/applications',
      oauthFailed: true,
    });
  });

  it('returns the signup card directly with scalar query values only', async () => {
    const element = await SignupPage({
      searchParams: Promise.resolve({ next: ['/unsafe', '/home'], error: ['x'] }),
    });
    expect(element.type).toBe(AuthCard);
    expect(element.props).toMatchObject({
      mode: 'signup',
      initialNext: undefined,
      oauthFailed: false,
    });
  });
});
