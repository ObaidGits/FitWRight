import * as React from 'react';
import type { Metadata } from 'next';
import { AuthCard } from '@/components/auth/auth-card';

export const metadata: Metadata = { title: 'Sign in — FitWright' };

export default function LoginPage() {
  return (
    <React.Suspense fallback={null}>
      <AuthCard mode="login" />
    </React.Suspense>
  );
}
