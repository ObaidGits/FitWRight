import type { Metadata } from 'next';
import { AuthCard } from '@/components/auth/auth-card';

export const metadata: Metadata = { title: 'Sign in — FitWright' };

type LoginPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const next = typeof params.next === 'string' ? params.next : undefined;
  const error = typeof params.error === 'string' ? params.error : undefined;

  return <AuthCard mode="login" initialNext={next} oauthFailed={error === 'oauth_failed'} />;
}
