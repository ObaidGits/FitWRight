import * as React from 'react';
import type { Metadata } from 'next';
import { VerifyEmailCard } from '@/components/auth/verify-email-card';

export const metadata: Metadata = { title: 'Verify email - FitWright' };

export default function VerifyPage() {
  return (
    <React.Suspense fallback={null}>
      <VerifyEmailCard />
    </React.Suspense>
  );
}
