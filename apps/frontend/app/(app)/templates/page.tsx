'use client';

/**
 * Resume Template Library - browse, filter, preview, and pick a template.
 *
 * Selecting a template records it as the preferred template (localStorage) and
 * sends the user into the wizard, which opens already rendered in that design.
 * Recommendations are personalized from the user's master resume when available.
 */
import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';

import { TemplateGallery } from '@/components/resume/template-gallery';
import { setPreferredTemplateId } from '@/lib/resume/preferred-template';
import { signalFromResume, type RecommendSignal } from '@/lib/resume/template-recommend';
import type { ResumeTemplate } from '@/lib/resume/template-catalog';
import { fetchResume, fetchResumeList } from '@/lib/api/resume';
import { queryKeys } from '@/lib/query/client';
import type { ResumeData } from '@/components/dashboard/resume-component';

/** Best-effort: derive a recommendation signal from the user's master resume. */
function useRecommendSignal(): RecommendSignal | undefined {
  const { data } = useQuery({
    queryKey: [...queryKeys.resumes, 'master-for-templates'],
    queryFn: async (): Promise<RecommendSignal | null> => {
      const list = await fetchResumeList(true);
      const master = list.find((r) => r.is_master) ?? list[0];
      if (!master) return null;
      const full = await fetchResume(master.resume_id);
      const processed = full.processed_resume as ResumeData | null;
      return processed ? signalFromResume(processed) : null;
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  return data ?? undefined;
}

export default function TemplatesPage() {
  const router = useRouter();
  const recommendSignal = useRecommendSignal();

  const handleSelect = React.useCallback(
    (template: ResumeTemplate) => {
      setPreferredTemplateId(template.id);
      router.push('/wizard');
    },
    [router]
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Resume templates</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Pick a professionally designed, ATS-aware template. Every preview is the real renderer,
            so what you see is exactly what you&apos;ll export.
          </p>
        </div>
        <Link href="/samples" className="text-sm font-medium text-[var(--at-ai)] hover:underline">
          Browse resume samples -&gt;
        </Link>
      </header>
      <TemplateGallery
        onSelect={handleSelect}
        recommendSignal={recommendSignal}
        ctaLabel="Use this template"
      />
    </div>
  );
}
