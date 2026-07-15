import type { Metadata } from 'next';
import { JsonLd } from '@/lib/seo/json-ld';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { breadcrumbSchema } from '@/lib/seo/structured-data';

export const metadata: Metadata = buildMetadata({
  title: 'Privacy Policy',
  description:
    'How FitWright handles your data: resumes stay in your own database, your AI provider API key is encrypted at rest, and you can delete everything at any time.',
  path: '/privacy',
  keywords: KEYWORDS.privacy,
});

export default function PrivacyPage() {
  return (
    <article className="mx-auto w-full max-w-3xl px-4 py-16 md:px-8">
      <JsonLd
        data={breadcrumbSchema([
          { name: 'Home', path: '/' },
          { name: 'Privacy Policy', path: '/privacy' },
        ])}
      />
      <h1 className="text-3xl font-semibold">Privacy Policy</h1>
      <p className="mt-2 text-sm text-[var(--muted-foreground)]">
        Last updated {new Date().getFullYear()}
      </p>
      <div className="mt-8 space-y-6 text-[var(--foreground)]">
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Your data</h2>
          <p className="text-[var(--muted-foreground)]">
            FitWright stores your resumes and job descriptions so it can tailor them. Your AI
            provider API key is encrypted at rest and never returned to your browser.
          </p>
        </section>
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">AI processing</h2>
          <p className="text-[var(--muted-foreground)]">
            Tailoring uses the AI provider you configure with your own key. Your resume content is
            sent to that provider only when you initiate a generation.
          </p>
        </section>
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Deletion</h2>
          <p className="text-[var(--muted-foreground)]">
            You can delete any resume or reset all data at any time from Settings. Deletion removes
            the associated records.
          </p>
        </section>
      </div>
    </article>
  );
}
