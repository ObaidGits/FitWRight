import type { Metadata } from 'next';

import { SampleGallery } from '@/components/resume/sample-gallery';

export const metadata: Metadata = {
  title: 'Resume Samples',
  description:
    'Browse professionally written resume samples across software, design, finance, healthcare, and more. Preview and use any sample as a starting point.',
  alternates: { canonical: '/samples' },
  openGraph: {
    title: 'Resume Samples - FitWright',
    description: 'Professionally written resume examples you can preview and use instantly.',
    type: 'website',
  },
};

export default function SamplesPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Resume samples</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Realistic, professionally written examples. Preview one, then use it as the starting point
          for your own resume - rendered in a matching template.
        </p>
      </header>
      <SampleGallery />
    </div>
  );
}
