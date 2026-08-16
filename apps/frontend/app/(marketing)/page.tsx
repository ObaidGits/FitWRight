/**
 * Landing page (Task 4 / Req 4) - premium, AI-native, story-driven.
 * Hero -> proof -> problem -> how it works -> AI analysis -> capabilities ->
 * tracking -> bring-your-own-key + providers -> privacy + control -> FAQ ->
 * final CTA.
 * Truthful by design: product demonstrations and real capabilities only -
 * no testimonials, fake stats, or manufactured social proof.
 */
import Link from 'next/link';
import { redirect } from 'next/navigation';
import type { Metadata } from 'next';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Gauge from 'lucide-react/dist/esm/icons/gauge';
import Mail from 'lucide-react/dist/esm/icons/mail';
import MessageSquare from 'lucide-react/dist/esm/icons/message-square';
import LayoutTemplate from 'lucide-react/dist/esm/icons/layout-template';
import Layers from 'lucide-react/dist/esm/icons/layers';
import Upload from 'lucide-react/dist/esm/icons/upload';
import FileSearch from 'lucide-react/dist/esm/icons/file-search-2';
import PenLine from 'lucide-react/dist/esm/icons/pen-line';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import KeyRound from 'lucide-react/dist/esm/icons/key-round';
import Lock from 'lucide-react/dist/esm/icons/lock';
import BadgeCheck from 'lucide-react/dist/esm/icons/badge-check';
import Brain from 'lucide-react/dist/esm/icons/brain';
import Target from 'lucide-react/dist/esm/icons/target';
import ListChecks from 'lucide-react/dist/esm/icons/list-checks';
import Eye from 'lucide-react/dist/esm/icons/eye';
import Compass from 'lucide-react/dist/esm/icons/compass';
import MousePointerClick from 'lucide-react/dist/esm/icons/mouse-pointer-click';
import Languages from 'lucide-react/dist/esm/icons/languages';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Reveal } from '@/components/marketing/reveal';
import { ContactCta } from '@/components/marketing/contact-cta';
import { Hero } from '@/components/marketing/hero';
import { HomepageProof } from '@/components/marketing/homepage-proof';
import { Faq } from '@/components/marketing/faq';
import { HomePricing } from '@/components/marketing/home-pricing';
import { LANDING_FAQS } from '@/components/marketing/faq-data';
import { AnalysisMock, KanbanMock, ResumeDocMock } from '@/components/marketing/mockups';
import { CAPABILITY_NAV } from '@/components/marketing/capabilities-data';
import { JsonLd } from '@/lib/seo/json-ld';
import { SUPPORT_EMAIL } from '@/lib/seo/config';
import { SINGLE_USER_MODE, APP_ENTRY_HREF } from '@/lib/config/auth';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { OG_IMAGE, TWITTER_IMAGE } from '@/lib/seo/config';
import {
  softwareApplicationSchema,
  softwareSourceCodeSchema,
  faqPageSchema,
  howToSchema,
  breadcrumbSchema,
} from '@/lib/seo/structured-data';

export const metadata: Metadata = {
  // Absolute title: the home page owns the full brand headline and must not get
  // the "- FitWright" template suffix (which would duplicate the brand name).
  title: { absolute: 'FitWright - AI Resume Builder & Tailor' },
  description:
    'Tailor your resume to every job with AI. Honest ATS scoring, cover letters, interview prep, and an application tracker. Bring your own API key.',
  keywords: [...KEYWORDS.landing],
  alternates: { canonical: '/' },
  openGraph: {
    title: 'FitWright - AI Resume Builder & Tailor',
    description:
      'Tailor your resume to every job with AI. Honest ATS scoring, cover letters, interview prep, and application tracking. Bring your own API key.',
    url: '/',
    type: 'website',
    images: [OG_IMAGE],
  },
  twitter: {
    card: 'summary_large_image',
    images: [TWITTER_IMAGE],
  },
};

const PROBLEMS = [
  {
    icon: Target,
    title: 'Generic resumes get skipped',
    body: 'One resume sent to every role rarely matches what any single job is actually asking for.',
  },
  {
    icon: FileSearch,
    title: 'ATS filters reject good candidates',
    body: 'Applicant tracking systems screen on keywords and structure before a human ever reads it.',
  },
  {
    icon: Layers,
    title: 'Tailoring by hand takes hours',
    body: 'Rewriting bullets, matching keywords, and writing a cover letter for every job does not scale.',
  },
];

const STEPS = [
  {
    icon: Upload,
    n: '01',
    title: 'Add your resume',
    body: 'Upload a PDF/DOCX or build one with the guided wizard.',
  },
  {
    icon: FileSearch,
    n: '02',
    title: 'Paste the job',
    body: 'FitWright analyzes the role and extracts what matters - before generating.',
  },
  {
    icon: Sparkles,
    n: '03',
    title: 'Tailor with AI',
    body: 'Get a reshaped resume with a match score and a clear, reviewable diff.',
  },
  {
    icon: PenLine,
    n: '04',
    title: 'Refine & export',
    body: 'Tweak anything, then export a clean, ATS-friendly PDF.',
  },
  {
    icon: Layers,
    n: '05',
    title: 'Apply & track',
    body: 'Manage every application from applied to offer on a Kanban board.',
  },
];

const CAPABILITIES = [
  {
    icon: Sparkles,
    title: 'Contextual AI',
    body: 'Ask AI to rewrite, shorten, or quantify any single bullet - preview the change before it applies.',
  },
  {
    icon: Gauge,
    title: 'Honest ATS score',
    body: 'A real match score with keyword, skills, and section sub-scores - plus exactly what is missing.',
  },
  {
    icon: Compass,
    title: 'Job discovery',
    body: 'Search 14 job boards at once and get roles ranked against your actual resume, not just keywords.',
  },
  {
    icon: MousePointerClick,
    title: 'Application autofill',
    body: 'A browser extension fills Greenhouse, Lever, Ashby, Workday and more from your profile. It never submits - you review first.',
  },
  {
    icon: MessageSquare,
    title: 'Application answers',
    body: 'Drafts those open-ended "why this company?" boxes from your resume and the job description.',
  },
  {
    icon: Mail,
    title: 'Cover letters',
    body: 'Generate a tailored cover letter grounded in your resume and the job, then export to PDF.',
  },
  {
    icon: MessageSquare,
    title: 'Interview prep',
    body: 'Resume-grounded questions, talking points, and role-fit analysis on demand.',
  },
  {
    icon: LayoutTemplate,
    title: 'Polished templates',
    body: 'Multiple templates with a live preview that matches the exported PDF exactly.',
  },
  {
    icon: Languages,
    title: 'Six languages',
    body: 'Use the app - and generate resumes and cover letters - in English, Spanish, Chinese, Japanese, French or Portuguese.',
  },
  {
    icon: BadgeCheck,
    title: 'Truthful by design',
    body: 'AI reshapes what you already have. It never invents experience or credentials.',
  },
];

const PROVIDERS = [
  'OpenAI',
  'Anthropic',
  'Google Gemini',
  'OpenRouter',
  'DeepSeek',
  'Groq',
  'Ollama (local)',
  'OpenAI-compatible',
];

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

export default function LandingPage() {
  // Single-user mode has no marketing/acquisition surface - the owner should
  // land directly in their workspace, not on a public landing page with
  // Sign in / Sign up affordances.
  if (SINGLE_USER_MODE) redirect('/home');
  return (
    <main>
      {/* Product + FAQ + breadcrumb structured data. Organization/WebSite are
          emitted site-wide in the root layout and referenced here by @id. */}
      <JsonLd
        data={[
          softwareApplicationSchema(),
          softwareSourceCodeSchema(),
          faqPageSchema(LANDING_FAQS),
          // Mirrors the visible "How it works" section (single source: STEPS).
          howToSchema({
            name: 'How to tailor your resume to a job with FitWright',
            description:
              'Add your resume, paste the job description, and let FitWright tailor it with AI - then refine, export, and track your application.',
            steps: STEPS.map((s) => ({ name: s.title, text: s.body })),
          }),
          breadcrumbSchema([{ name: 'Home', path: '/' }]),
        ]}
      />
      {/* Progressive enhancement: reveal content even without JS. */}
      <noscript>
        <style>{`.reveal{opacity:1 !important;transform:none !important}`}</style>
      </noscript>

      <Hero />
      <HomepageProof />

      {/* The problem */}
      <section className="border-t border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto w-full max-w-6xl px-4 py-20 md:px-8">
          <Reveal as="div">
            <SectionHeading
              eyebrow="The problem"
              title="Sending the same resume everywhere doesn't work"
              sub="Roles differ, and the systems that screen you optimize for a specific fit. Manual tailoring is the fix - but it's slow."
            />
          </Reveal>
          <div className="mt-12 grid gap-4 md:grid-cols-3">
            {PROBLEMS.map((p, i) => {
              const Icon = p.icon;
              return (
                <Reveal key={p.title} delay={i * 80}>
                  <Card className="h-full p-6">
                    <span className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--destructive)]/10 text-[var(--destructive)]">
                      <Icon className="h-5 w-5" />
                    </span>
                    <h3 className="mt-4 text-base font-semibold">{p.title}</h3>
                    <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">{p.body}</p>
                  </Card>
                </Reveal>
              );
            })}
          </div>
          <Reveal className="mt-10 text-center" delay={120}>
            <p className="mx-auto max-w-xl text-lg font-medium">
              FitWright does the tailoring for you - in seconds, grounded in your real experience.
            </p>
          </Reveal>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto w-full max-w-6xl px-4 py-20 md:px-8">
        <Reveal>
          <SectionHeading
            eyebrow="How it works"
            title="From resume to offer, in one place"
            sub="A guided workflow that keeps every application organized."
          />
        </Reveal>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            return (
              <Reveal key={s.n} delay={i * 70}>
                <div className="relative h-full rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-5">
                  <div className="flex items-center justify-between">
                    <span className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                      <Icon className="h-[18px] w-[18px]" />
                    </span>
                    <span className="text-xs font-semibold text-[var(--muted-foreground)]">
                      {s.n}
                    </span>
                  </div>
                  <h3 className="mt-4 text-sm font-semibold">{s.title}</h3>
                  <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
                    {s.body}
                  </p>
                </div>
              </Reveal>
            );
          })}
        </div>
      </section>

      {/* AI analysis showcase */}
      <section className="border-y border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto grid w-full max-w-6xl items-center gap-12 px-4 py-20 md:px-8 lg:grid-cols-2">
          <Reveal>
            <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
              AI analysis
            </span>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">
              See the fit before you apply
            </h2>
            <p className="mt-3 text-[var(--muted-foreground)]">
              FitWright reads the job, extracts the important bits, and shows what your resume
              already covers - and what is still missing.
            </p>
            <ul className="mt-6 space-y-3">
              {[
                { icon: Brain, t: 'Role detection + keyword extraction' },
                { icon: ListChecks, t: 'Matched vs. missing skills' },
                { icon: Gauge, t: 'Clear sub-scores' },
                { icon: Eye, t: 'Every change stays reviewable' },
              ].map((f) => {
                const Icon = f.icon;
                return (
                  <li key={f.t} className="flex items-center gap-3 text-sm">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--primary)]/10 text-[var(--primary)]">
                      <Icon className="h-4 w-4" />
                    </span>
                    {f.t}
                  </li>
                );
              })}
            </ul>
          </Reveal>
          <Reveal delay={100}>
            <AnalysisMock />
          </Reveal>
        </div>
      </section>

      {/* Capabilities */}
      <section id="features" className="mx-auto w-full max-w-6xl px-4 py-20 md:px-8">
        <Reveal>
          <SectionHeading
            eyebrow="Capabilities"
            title="Everything to land the interview"
            sub="A complete toolkit, not a form with a button."
          />
        </Reveal>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map((c, i) => {
            const Icon = c.icon;
            return (
              <Reveal key={c.title} delay={(i % 3) * 80}>
                <Card className="group h-full p-6 transition-shadow hover:shadow-[var(--shadow-at-e2)]">
                  <span className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                    <Icon className="h-5 w-5" />
                  </span>
                  <h3 className="mt-4 text-base font-semibold">{c.title}</h3>
                  <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">{c.body}</p>
                </Card>
              </Reveal>
            );
          })}
        </div>
      </section>

      {/* Application tracking */}
      <section className="border-y border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto grid w-full max-w-6xl items-center gap-12 px-4 py-20 md:px-8 lg:grid-cols-2">
          <Reveal className="order-2 lg:order-1" delay={100}>
            <KanbanMock />
          </Reveal>
          <Reveal className="order-1 lg:order-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
              Stay organized
            </span>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">
              Run your whole search from one board
            </h2>
            <p className="mt-3 text-[var(--muted-foreground)]">
              Each tailored resume becomes an application. Move roles across stages, keep notes
              together, and stay on top of the search without extra tabs.
            </p>
            <div className="mt-6">
              <Button asChild variant="outline">
                <Link href={APP_ENTRY_HREF}>
                  Explore the workspace <ArrowRight className="h-4 w-4" />
                </Link>
              </Button>
            </div>
          </Reveal>
        </div>
      </section>

      {/* Bring your own key + providers */}
      <section className="mx-auto w-full max-w-6xl px-4 py-20 md:px-8">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <Reveal>
            <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
              Your keys, your control
            </span>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">
              Bring your own API key
            </h2>
            <p className="mt-3 text-[var(--muted-foreground)]">
              Connect the model you already use. You control the cost, the data, and the output.
              Prefer local runs? Ollama works too.
            </p>
            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              {[
                { icon: KeyRound, t: 'You own the key' },
                { icon: Gauge, t: 'You control cost' },
                { icon: Brain, t: 'Any model you like' },
              ].map((f) => {
                const Icon = f.icon;
                return (
                  <div
                    key={f.t}
                    className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-3"
                  >
                    <Icon className="h-5 w-5 text-[var(--primary)]" />
                    <p className="mt-2 text-xs font-medium">{f.t}</p>
                  </div>
                );
              })}
            </div>
          </Reveal>
          <Reveal delay={100}>
            <Card className="p-6">
              <p className="mb-4 text-sm font-semibold text-[var(--muted-foreground)]">
                Works with your favorite providers
              </p>
              <div className="flex flex-wrap gap-2">
                {PROVIDERS.map((p) => (
                  <span
                    key={p}
                    className="inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--at-surface-2)] px-3 py-1.5 text-xs font-medium"
                  >
                    <span className="h-2 w-2 rounded-full bg-[var(--at-ai)]" />
                    {p}
                  </span>
                ))}
              </div>
            </Card>
          </Reveal>
        </div>
      </section>

      {/* Privacy + control */}
      <section className="border-y border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto grid w-full max-w-6xl gap-4 px-4 py-20 md:grid-cols-2 md:px-8">
          <Reveal>
            <Card className="h-full p-8">
              <span className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-at-lg)] bg-[var(--at-success)]/15 text-[var(--at-success)]">
                <Lock className="h-5 w-5" />
              </span>
              <h3 className="mt-4 text-xl font-semibold">Private by default</h3>
              <p className="mt-2 text-sm text-[var(--muted-foreground)]">
                Your resume content stays in your own database. Nothing is sent anywhere except the
                AI provider you choose.
              </p>
              <ul className="mt-4 space-y-2 text-sm">
                {['You own your data', 'Encrypted key storage', 'No hidden processing'].map((t) => (
                  <li key={t} className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-[var(--at-success)]" /> {t}
                  </li>
                ))}
              </ul>
            </Card>
          </Reveal>
          <Reveal delay={100}>
            <Card className="h-full p-8">
              <span className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-at-lg)] bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                <ShieldCheck className="h-5 w-5" />
              </span>
              <h3 className="mt-4 text-xl font-semibold">Simple to trust</h3>
              <p className="mt-2 text-sm text-[var(--muted-foreground)]">
                The interface stays clear, the flows stay direct, and the output stays easy to
                review.
              </p>
            </Card>
          </Reveal>
        </div>
      </section>

      {/* Pricing - interactive, because the visitor's real question is "which plan do I
          need?", not "what are your tiers?". Prices are fetched server-side from the same
          admin-editable rows the app charges from, so this cannot advertise a stale number. */}
      <section id="pricing" className="border-y border-[var(--border)] bg-[var(--at-surface-2)]">
        <div className="mx-auto w-full max-w-6xl px-4 py-20 md:px-8">
          <Reveal>
            <SectionHeading
              eyebrow="Pricing"
              title="Pay for the writing, not the searching"
              sub="Start free. Searching never costs credits - only writing does."
            />
          </Reveal>
          <Reveal delay={80}>
            <HomePricing />
          </Reveal>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="mx-auto w-full max-w-6xl px-4 py-20 md:px-8">
        <Reveal>
          <SectionHeading eyebrow="FAQ" title="Questions, answered" />
        </Reveal>
        <Reveal className="mt-10" delay={80}>
          <Faq />
        </Reveal>
      </section>

      {/* Final CTA */}
      <section className="mx-auto w-full max-w-6xl px-4 pb-24 md:px-8">
        <Reveal>
          <div className="relative overflow-hidden rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] px-6 py-14 text-center shadow-[var(--shadow-at-e2)]">
            <div aria-hidden className="pointer-events-none absolute inset-0">
              <div
                className="at-blob left-1/4 top-0 h-40 w-40"
                style={{ background: 'var(--at-ai)' }}
              />
              <div
                className="at-blob right-1/4 bottom-0 h-40 w-40"
                style={{ background: 'var(--primary)', animationDelay: '-8s' }}
              />
            </div>
            <div className="relative">
              <div className="mx-auto mb-6 flex justify-center gap-3">
                <ResumeDocMock className="at-float-sm hidden rotate-[-6deg] sm:block" />
                <ResumeDocMock className="at-float hidden translate-y-2 rotate-[4deg] sm:block" />
              </div>
              <h2 className="mx-auto max-w-xl text-3xl font-semibold tracking-tight md:text-4xl">
                Tailor your next application in seconds
              </h2>
              <p className="mx-auto mt-3 max-w-md text-[var(--muted-foreground)]">
                Private, focused, and built for faster applications. Bring your key and start now.
              </p>
              <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                <Button asChild size="lg">
                  <Link href={APP_ENTRY_HREF}>
                    <Sparkles className="h-4 w-4" /> Get started
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <a href="#contact-cta-heading">
                    <ArrowRight className="h-4 w-4" /> Contact
                  </a>
                </Button>
              </div>
            </div>
          </div>
        </Reveal>
      </section>

      {/* Let's work together - personal contact CTA (introduces the developer
          and routes to /contact). Replaces the thin "About the developer"
          blurb with a richer, conversion-focused section right where the
          reader has just finished the product story. */}
      <ContactCta />

      {/* Footer */}
      <footer className="border-t border-[var(--border)]">
        <div className="mx-auto flex w-full max-w-6xl flex-col items-center justify-between gap-4 px-4 py-8 text-sm text-[var(--muted-foreground)] md:flex-row md:px-8">
          <span className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-[var(--radius-at-sm)] bg-[var(--primary)] text-[10px] font-bold text-[var(--primary-foreground)]">
              FW
            </span>
            © {new Date().getFullYear()} FitWright
          </span>
          <nav
            className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2 md:justify-end"
            aria-label="Footer"
          >
            {CAPABILITY_NAV.map((cap) => (
              <Link key={cap.slug} href={`/${cap.slug}`} className="hover:text-[var(--foreground)]">
                {cap.label}
              </Link>
            ))}
            <Link href="/contact" className="hover:text-[var(--foreground)]">
              Contact
            </Link>
            <a href={`mailto:${SUPPORT_EMAIL}`} className="hover:text-[var(--foreground)]">
              Email
            </a>
            <Link href="/privacy" className="hover:text-[var(--foreground)]">
              Privacy
            </Link>
            <Link href="/terms" className="hover:text-[var(--foreground)]">
              Terms
            </Link>
          </nav>
        </div>
      </footer>
    </main>
  );
}
