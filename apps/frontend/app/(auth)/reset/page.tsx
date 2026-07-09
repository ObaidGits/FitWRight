import * as React from 'react';
import type { Metadata } from 'next';
import { ResetCard } from '@/components/auth/reset-card';

export const metadata: Metadata = { title: 'New password — FitWright' };

export default function ResetPage() {
  return (
    <React.Suspense fallback={null}>
      <ResetCard />
    </React.Suspense>
  );
}
