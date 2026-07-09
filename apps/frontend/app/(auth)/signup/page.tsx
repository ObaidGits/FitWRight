import * as React from 'react';
import type { Metadata } from 'next';
import { AuthCard } from '@/components/auth/auth-card';

export const metadata: Metadata = { title: 'Sign up — FitWright' };

export default function SignupPage() {
  return (
    <React.Suspense fallback={null}>
      <AuthCard mode="signup" />
    </React.Suspense>
  );
}
