import * as React from 'react';
import type { Metadata } from 'next';
import { EmailChangeConfirmCard } from '@/components/auth/email-change-confirm-card';

export const metadata: Metadata = { title: 'Confirm email change — FitWright' };

export default function VerifyEmailPage() {
  return (
    <React.Suspense fallback={null}>
      <EmailChangeConfirmCard />
    </React.Suspense>
  );
}
