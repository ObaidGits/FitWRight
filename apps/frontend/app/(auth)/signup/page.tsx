import type { Metadata } from 'next';
import { AuthCard } from '@/components/auth/auth-card';

export const metadata: Metadata = { title: 'Sign up - FitWright' };

type SignupPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function SignupPage({ searchParams }: SignupPageProps) {
  const params = await searchParams;
  const next = typeof params.next === 'string' ? params.next : undefined;
  const error = typeof params.error === 'string' ? params.error : undefined;

  return <AuthCard mode="signup" initialNext={next} oauthFailed={error === 'oauth_failed'} />;
}
