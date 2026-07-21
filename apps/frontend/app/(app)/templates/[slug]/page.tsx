import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { ResumeDocument } from '@/components/resume/resume-document';
import { UseTemplateButton } from '@/components/resume/resume-cta-buttons';
import { JsonLd } from '@/lib/seo/json-ld';
import { breadcrumbSchema } from '@/lib/seo/structured-data';
import { absoluteUrl } from '@/lib/seo/config';
import { SAMPLE_RESUME } from '@/lib/resume/sample-resume';
import {
  RESUME_TEMPLATES,
  TEMPLATE_CATEGORIES,
  getTemplateById,
  templateToSettings,
} from '@/lib/resume/template-catalog';
import { RESUME_SAMPLES } from '@/lib/resume/sample-catalog';

interface Params {
  params: Promise<{ slug: string }>;
}

export function generateStaticParams() {
  return RESUME_TEMPLATES.map((t) => ({ slug: t.id }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  const t = getTemplateById(slug);
  if (!t) return { title: 'Template not found' };
  const title = `${t.name} Resume Template`;
  return {
    title,
    description: t.description,
    alternates: { canonical: `/templates/${t.id}` },
    openGraph: { title: `${title} - FitWright`, description: t.description, type: 'article' },
  };
}

function categoryLabel(id: string): string {
  return TEMPLATE_CATEGORIES.find((c) => c.id === id)?.label ?? id;
}

export default async function TemplateDetailPage({ params }: Params) {
  const { slug } = await params;
  const t = getTemplateById(slug);
  if (!t) notFound();

  const settings = templateToSettings(t);
  const related = RESUME_TEMPLATES.filter((x) => x.id !== t.id && x.category === t.category).slice(
    0,
    3
  );
  const relatedSampleList = RESUME_SAMPLES.filter((s) => s.recommendedTemplateId === t.id).slice(
    0,
    3
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <JsonLd
        data={[
          {
            '@context': 'https://schema.org',
            '@type': 'CreativeWork',
            name: `${t.name} Resume Template`,
            description: t.description,
            keywords: [...t.tags, ...t.recommendedFor].join(', '),
            url: absoluteUrl(`/templates/${t.id}`),
            genre: categoryLabel(t.category),
          },
          breadcrumbSchema([
            { name: 'Home', path: '/' },
            { name: 'Templates', path: '/templates' },
            { name: t.name, path: `/templates/${t.id}` },
          ]),
        ]}
      />

      <nav aria-label="Breadcrumb" className="text-sm text-[var(--muted-foreground)]">
        <Link href="/templates" className="hover:underline">
          Templates
        </Link>{' '}
        / <span className="text-[var(--foreground)]">{t.name}</span>
      </nav>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="order-2 lg:order-1">
          <div className="overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-4">
            <ResumeDocument data={SAMPLE_RESUME} settings={settings} maxPages={2} />
          </div>
        </div>

        <aside className="order-1 space-y-4 lg:order-2">
          <div>
            <h1 className="text-2xl font-semibold">{t.name}</h1>
            <p className="text-sm text-[var(--muted-foreground)]">{t.description}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{categoryLabel(t.category)}</Badge>
            <Badge variant="success">ATS {t.atsScore}/5</Badge>
            <Badge variant={t.photoSupport === 'none' ? 'neutral' : 'primary'}>
              {t.photoSupport === 'none'
                ? 'No photo'
                : t.photoSupport === 'required'
                  ? 'Photo'
                  : 'Photo optional'}
            </Badge>
          </div>
          <p className="text-xs text-[var(--muted-foreground)]">{t.atsNote}</p>

          <div>
            <h2 className="mb-1 text-sm font-semibold text-[var(--muted-foreground)]">
              Recommended for
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {t.recommendedFor.map((r) => (
                <Badge key={r} variant="neutral">
                  {r}
                </Badge>
              ))}
            </div>
          </div>

          <UseTemplateButton templateId={t.id} className="w-full" />

          {relatedSampleList.length > 0 && (
            <div className="pt-2">
              <h2 className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">
                Matching samples
              </h2>
              <ul className="space-y-1 text-sm">
                {relatedSampleList.map((s) => (
                  <li key={s.id}>
                    <Link href={`/samples/${s.id}`} className="text-[var(--at-ai)] hover:underline">
                      {s.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {related.length > 0 && (
            <div>
              <h2 className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">
                Related templates
              </h2>
              <ul className="space-y-1 text-sm">
                {related.map((r) => (
                  <li key={r.id}>
                    <Link
                      href={`/templates/${r.id}`}
                      className="text-[var(--at-ai)] hover:underline"
                    >
                      {r.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Button asChild variant="outline" className="w-full">
            <Link href="/templates">Back to gallery</Link>
          </Button>
        </aside>
      </div>
    </div>
  );
}
