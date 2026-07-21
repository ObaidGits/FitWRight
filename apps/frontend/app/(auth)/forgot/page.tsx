import * as React from 'react';
import type { Metadata } from 'next';
import { ForgotCard } from '@/components/auth/forgot-card';

export const metadata: Metadata = { title: 'Reset password - FitWright' };

export default function ForgotPage() {
  return (
    <React.Suspense fallback={null}>
      <ForgotCard />
    </React.Suspense>
  );
}
