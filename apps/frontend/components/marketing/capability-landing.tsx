/**
 * <CapabilityLanding> — the single renderer for every feature landing page.
 *
 * Server component (SEO-first): emits a WebPage + HowTo + FAQPage +
 * BreadcrumbList entity graph, a single <h1>, semantic sections, and a
 * cross-linked "related capabilities" block that turns the feature pages into a
 * proper topic cluster (hub = home, spokes = capabilities). All content comes
 * from CAPABILITIES data — no per-page duplication.
 */
import Link from 'next/link';
import type { ComponentType } from 'react';
import Upload from 'lucide-react/dist/esm/icons/upload';
import FileSearch from 'lucide-react/dist/esm/icons/file-search-2';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import PenLine from 'lucide-react/dist/esm/icons/pen-line';
import Gauge from 'lucide-react/dist/esm/icons/gauge';
import ListChecks from 'lucide-react/dist/esm/icons/list-checks';
import Eye from 'lucide-react/dist/esm/icons/eye';
import BadgeCheck from 'lucide-react/dist/esm/icons/badge-check';
import Mail from 'lucide-react/dist/esm/icons/mail';
import MessageSquare from 'lucide-react/dist/esm/icons/message-square';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import Target from 'lucide-react/dist/esm/icons/target';
import Brain from 'lucide-react/dist/esm/icons/brain';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Reveal } from '@/components/marketing/reveal';
import { JsonLd } from '@/lib/seo/json-ld';
import {
  webPageSchema,
  howToSchema,
  faqPageSchema,
  breadcrumbSchema,
} from '@/lib/seo/structured-data';
import {
  type Capability,
  type CapabilityIcon,
  CAPABILITY_NAV,
  capabilityPath,
} from '@/components/marketing/capabilities-data';

const ICONS: Record<CapabilityIcon, ComponentType<{ className?: string }>> = {
  upload: Upload,
  search: FileSearch,
  sparkles: Sparkles,
  pen: PenLine,
  gauge: Gauge,
  list: ListChecks,
  eye: Eye,
  badge: BadgeCheck,
  mail: Mail,
  message: MessageSquare,
  shield: ShieldCheck,
  target: Target,
  brain: Brain,
  file: FileText,
};

function SectionHeading({ eyebrow, title, sub }: { eyebrow: string; title: string; sub?: string }) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
        {eyebrow}
      </span>
      <h2 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">{title}</h2>
      {sub && <p className="mt-3 text-[var(--muted-foreground)]">{sub}</p>}
    </div>
  );
}

export function CapabilityLanding({ capability: c }: { capability: Capability }) {
  const path = capabilityPath(c.slug);
  const related = CAPABILITY_NAV.filter((n) => n.slug !== c.slug);

  return (
    <main>
      <JsonLd
        data={[
          webPageSchema({ name: c.h1, path, description: c.metaDescription }),
          howToSchema({
            name: c.howToName,
            description: c.howToDescription,
            steps: c.steps.map((s) => ({ name: s.title, text: s.body })),
          }),
          faqPageSchema(c.faqs),
          breadcrumbSchema([
            { name: 'Home', path: '/' },
            { name: c.eyebrow, path },
          ]),
        ]}
      />

      {/* Hero */}
      <section className="mx-auto w-full max-w-6xl px-4 pt-16 pb-8 md:px-8 md:pt-24">
        <Reveal>
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
            {c.eyebrow}
          </span>
          <h1 className="mt-3 max-w-3xl text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
            {c.h1}
          </h1>
          <p className="mt-5 max-w-2xl text-lg text-[var(--muted-foreground)]">{c.heroSub}</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild size="lg">
              <Link href="/home">
                <Sparkles className="h-4 w-4" /> {c.primaryCtaLabel}{' '}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link href="/#how">See how it works</Link>
            </Button>
          </div>
        </Reveal>
      </section>

      {/* Definition — entity-first, AI/LLM friendly */}
      <section className="border-y border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto w-full max-w-3xl px-4 py-16 md:px-8">
          <Reveal>
            <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">
              {c.definitionHeading}
            </h2>
            {c.definition.map((p) => (
              <p key={p.slice(0, 32)} className="mt-4 text-[var(--muted-foreground)]">
                {p}
              </p>
            ))}
          </Reveal>
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto w-full max-w-6xl px-4 py-16 md:px-8">
        <Reveal>
          <SectionHeading eyebrow="How it works" title={c.howToName} />
        </Reveal>
        <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {c.steps.map((s, i) => {
            const Icon = ICONS[s.icon];
            return (
              <Reveal key={s.title} as="li" className="list-none" delay={(i % 4) * 70}>
                <Card className="h-full p-6">
                  <div className="flex items-center justify-between">
                    <span className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                      <Icon className="h-5 w-5" />
                    </span>
                    <span className="text-xs font-semibold text-[var(--muted-foreground)]">
                      {String(i + 1).padStart(2, '0')}
                    </span>
                  </div>
                  <h3 className="mt-4 text-base font-semibold">{s.title}</h3>
                  <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">{s.body}</p>
                </Card>
              </Reveal>
            );
          })}
        </ol>
      </section>

      {/* Outcomes */}
      <section className="border-y border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto w-full max-w-6xl px-4 py-16 md:px-8">
          <Reveal>
            <SectionHeading eyebrow="What you get" title={c.outcomesHeading} sub={c.outcomesSub} />
          </Reveal>
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {c.outcomes.map((o, i) => {
              const Icon = ICONS[o.icon];
              return (
                <Reveal key={o.title} delay={(i % 4) * 70}>
                  <Card className="h-full p-6">
                    <span className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)]/10 text-[var(--primary)]">
                      <Icon className="h-5 w-5" />
                    </span>
                    <h3 className="mt-4 text-base font-semibold">{o.title}</h3>
                    <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">{o.body}</p>
                  </Card>
                </Reveal>
              );
            })}
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="mx-auto w-full max-w-3xl px-4 py-16 md:px-8">
        <Reveal>
          <h2 className="text-center text-3xl font-semibold tracking-tight md:text-4xl">
            {c.eyebrow} FAQ
          </h2>
        </Reveal>
        <dl className="mt-10 divide-y divide-[var(--border)] rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)]">
          {c.faqs.map((f) => (
            <div key={f.q} className="p-5">
              <dt className="text-sm font-semibold">{f.q}</dt>
              <dd className="mt-1.5 text-sm leading-relaxed text-[var(--muted-foreground)]">
                {f.a}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {/* CTA + related capabilities (topic cluster cross-links) */}
      <section className="mx-auto w-full max-w-6xl px-4 pb-24 md:px-8">
        <Reveal>
          <div className="rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] p-8 text-center shadow-[var(--shadow-at-e1)]">
            <h2 className="text-2xl font-semibold tracking-tight">{c.ctaHeading}</h2>
            <p className="mx-auto mt-2 max-w-md text-[var(--muted-foreground)]">{c.ctaSub}</p>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
              <Button asChild size="lg">
                <Link href="/home">
                  <Sparkles className="h-4 w-4" /> Get started
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link href="/">Explore all features</Link>
              </Button>
            </div>

            <div className="mt-8 border-t border-[var(--border)] pt-6">
              <p className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
                Related capabilities
              </p>
              <nav
                aria-label="Related capabilities"
                className="mt-3 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-sm"
              >
                {related.map((r) => (
                  <Link
                    key={r.slug}
                    href={capabilityPath(r.slug)}
                    className="text-[var(--primary)] hover:underline"
                  >
                    {r.label}
                  </Link>
                ))}
                <Link
                  href="/connect"
                  className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                >
                  Connect
                </Link>
                <Link
                  href="/contact"
                  className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                >
                  Contact
                </Link>
              </nav>
            </div>
          </div>
        </Reveal>
      </section>
    </main>
  );
}
