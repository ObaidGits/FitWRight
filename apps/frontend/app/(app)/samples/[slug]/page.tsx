import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { ResumeDocument } from '@/components/resume/resume-document';
import { UseSampleButton } from '@/components/resume/resume-cta-buttons';
import { JsonLd } from '@/lib/seo/json-ld';
import { breadcrumbSchema } from '@/lib/seo/structured-data';
import { absoluteUrl } from '@/lib/seo/config';
import { getSampleById, RESUME_SAMPLES, relatedSamples } from '@/lib/resume/sample-catalog';
import { getTemplateById, templateToSettings } from '@/lib/resume/template-catalog';

interface Params {
  params: Promise<{ slug: string }>;
}

export function generateStaticParams() {
  return RESUME_SAMPLES.map((s) => ({ slug: s.id }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  const sample = getSampleById(slug);
  if (!sample) return { title: 'Sample not found' };
  const title = `${sample.name} Resume Sample`;
  return {
    title,
    description: sample.description,
    alternates: { canonical: `/samples/${sample.id}` },
    openGraph: { title: `${title} — FitWright`, description: sample.description, type: 'article' },
  };
}

export default async function SampleDetailPage({ params }: Params) {
  const { slug } = await params;
  const sample = getSampleById(slug);
  if (!sample) notFound();

  const template = getTemplateById(sample.recommendedTemplateId);
  const settings = template ? templateToSettings(template) : undefined;
  const related = relatedSamples(sample);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <JsonLd
        data={[
          {
            '@context': 'https://schema.org',
            '@type': 'CreativeWork',
            name: `${sample.name} Resume Sample`,
            about: sample.role,
            description: sample.description,
            keywords: sample.tags.join(', '),
            url: absoluteUrl(`/samples/${sample.id}`),
            genre: sample.industry,
          },
          breadcrumbSchema([
            { name: 'Home', path: '/' },
            { name: 'Samples', path: '/samples' },
            { name: sample.name, path: `/samples/${sample.id}` },
          ]),
        ]}
      />

      <nav aria-label="Breadcrumb" className="text-sm text-[var(--muted-foreground)]">
        <Link href="/samples" className="hover:underline">
          Samples
        </Link>{' '}
        / <span className="text-[var(--foreground)]">{sample.name}</span>
      </nav>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="order-2 lg:order-1">
          <div className="overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-4">
            <ResumeDocument data={sample.data} settings={settings} maxPages={2} />
          </div>
        </div>

        <aside className="order-1 space-y-4 lg:order-2">
          <div>
            <h1 className="text-2xl font-semibold">{sample.name}</h1>
            <p className="text-sm text-[var(--muted-foreground)]">{sample.description}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{sample.industry}</Badge>
            <Badge variant="neutral">{sample.experienceLevel}</Badge>
            <Badge variant={sample.hasPhoto ? 'primary' : 'neutral'}>
              {sample.hasPhoto ? 'Photo' : 'No photo'}
            </Badge>
            <Badge variant="success">ATS {sample.atsScore}/5</Badge>
          </div>
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className="text-[var(--muted-foreground)]">Role</dt>
              <dd>{sample.role}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-[var(--muted-foreground)]">Career stage</dt>
              <dd className="capitalize">{sample.experienceLevel}</dd>
            </div>
          </dl>
          <UseSampleButton sample={sample} className="w-full" />
          {template && (
            <Button asChild variant="outline" className="w-full">
              <Link href={`/templates/${template.id}`}>View recommended template</Link>
            </Button>
          )}

          {related.length > 0 && (
            <div className="pt-2">
              <h2 className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">
                Related samples
              </h2>
              <ul className="space-y-1 text-sm">
                {related.map((r) => (
                  <li key={r.id}>
                    <Link href={`/samples/${r.id}`} className="text-[var(--at-ai)] hover:underline">
                      {r.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
