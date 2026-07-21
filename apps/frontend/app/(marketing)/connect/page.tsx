/**
 * Connect with the developer (marketing). A premium, human-centered hub to
 * reach out, give feedback, report bugs, request features, review the product,
 * or explore collaboration. Server component (SEO + JSON-LD + static markup);
 * the two forms are client islands. Reuses the atelier design system and the
 * production contact/review backends.
 */
import type { Metadata } from 'next';
import Link from 'next/link';
import Github from 'lucide-react/dist/esm/icons/github';
import Linkedin from 'lucide-react/dist/esm/icons/linkedin';
import Globe from 'lucide-react/dist/esm/icons/globe';
import MessageSquare from 'lucide-react/dist/esm/icons/message-square';
import Clock from 'lucide-react/dist/esm/icons/clock';
import MapPin from 'lucide-react/dist/esm/icons/map-pin';
import Brain from 'lucide-react/dist/esm/icons/brain';
import Layers from 'lucide-react/dist/esm/icons/layers';
import GitBranch from 'lucide-react/dist/esm/icons/git-branch';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import Handshake from 'lucide-react/dist/esm/icons/handshake';
import Compass from 'lucide-react/dist/esm/icons/compass';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';

import { Card } from '@/components/atelier/card';
import { Reveal } from '@/components/marketing/reveal';
import { ContactForm } from '@/components/contact/contact-form';
import { ReviewForm } from '@/components/connect/review-form';
import { JsonLd } from '@/lib/seo/json-ld';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { profilePageSchema, breadcrumbSchema } from '@/lib/seo/structured-data';

export const metadata: Metadata = buildMetadata({
  title: 'Connect - Ideas, feedback & collaboration',
  description:
    'Connect with the developer behind FitWright. Share feedback, report a bug, request a feature, leave a review, or start a collaboration. I read and reply to every message.',
  path: '/connect',
  keywords: KEYWORDS.connect,
  socialTitle: 'Connect with the developer - FitWright',
});

const CHANNELS = [
  {
    icon: Linkedin,
    label: 'LinkedIn',
    desc: 'Professional network - roles, intros, and opportunities.',
    href: 'https://www.linkedin.com/in/obaidullah-zeeshan/',
    external: true,
  },
  {
    icon: Github,
    label: 'GitHub',
    desc: "Code, open source, and this project's source.",
    href: 'https://github.com/ObaidGits',
    external: true,
  },
  {
    icon: MessageSquare,
    label: 'Send a message',
    desc: 'The fastest way to reach my inbox directly.',
    href: '/contact',
    external: false,
  },
  {
    icon: Globe,
    label: 'Résumé & portfolio',
    desc: 'More work, writing, and background.',
    href: 'https://obaidullah-zeeshan.dev',
    external: true,
  },
];

const COLLAB = [
  {
    icon: Brain,
    title: 'AI & LLM work',
    body: 'Applied AI features, evals, and pragmatic LLM systems.',
  },
  {
    icon: Layers,
    title: 'Full-stack builds',
    body: 'End-to-end product engineering, from schema to UI.',
  },
  {
    icon: GitBranch,
    title: 'Open source',
    body: 'Contributions, reviews, and building in the open.',
  },
  {
    icon: Compass,
    title: 'Advisory & mentorship',
    body: 'Architecture reviews and thoughtful technical guidance.',
  },
];

const FAQ = [
  {
    q: "What's the best reason to reach out?",
    a: 'Roles and collaborations, product feedback, bug reports, and feature ideas are all welcome - and genuinely read.',
  },
  {
    q: 'How fast will I hear back?',
    a: "Usually within 1-2 business days. If it's time-sensitive, say so in the message and I'll prioritize it.",
  },
  {
    q: 'Which channel should I use?',
    a: 'For anything substantive, the message form here reaches me directly. LinkedIn is great for professional intros; GitHub for code and issues.',
  },
];

function SectionHeading({ eyebrow, title, sub }: { eyebrow: string; title: string; sub?: string }) {
  return (
    <div className="max-w-2xl">
      <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
        {eyebrow}
      </span>
      <h2 className="mt-2 text-2xl font-semibold tracking-tight md:text-3xl">{title}</h2>
      {sub && <p className="mt-2 text-[var(--muted-foreground)]">{sub}</p>}
    </div>
  );
}

export default function ConnectPage() {
  return (
    <main className="relative overflow-hidden">
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 at-grid-bg opacity-50" />
        <div
          className="at-blob left-[-6rem] top-[-3rem] h-72 w-72"
          style={{ background: 'var(--at-ai)' }}
        />
        <div
          className="at-blob right-[-5rem] top-[8rem] h-64 w-64"
          style={{ background: 'var(--primary)', animationDelay: '-7s' }}
        />
      </div>

      <JsonLd
        data={[
          profilePageSchema('/connect'),
          breadcrumbSchema([
            { name: 'Home', path: '/' },
            { name: 'Connect', path: '/connect' },
          ]),
        ]}
      />

      <div className="relative mx-auto w-full max-w-6xl px-4 md:px-8">
        {/* Hero */}
        <Reveal className="pt-16 md:pt-24">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--at-success)]/40 bg-[var(--at-success)]/10 px-3 py-1 text-xs font-medium text-[var(--at-success)]">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-[var(--at-success)] opacity-70 motion-safe:animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--at-success)]" />
            </span>
            Open to collaboration &amp; new work
          </span>
          <h1 className="mt-6 max-w-3xl text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
            Every great product starts with a{' '}
            <span className="bg-gradient-to-r from-[var(--primary)] to-[var(--at-ai)] bg-clip-text text-transparent">
              conversation.
            </span>
          </h1>
          <p className="mt-5 max-w-xl text-lg text-[var(--muted-foreground)]">
            I'm Obaidullah - the engineer behind FitWright. Ideas, feedback, bug reports, or a role
            worth exploring: I read every message and reply thoughtfully. Your feedback genuinely
            shapes where this product goes next.
          </p>
        </Reveal>

        {/* Developer card + trust */}
        <Reveal className="mt-10" delay={80}>
          <Card className="grid gap-6 p-6 md:grid-cols-[auto_1fr] md:p-7">
            <div className="flex items-center gap-4">
              <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-[var(--radius-at-xl)] bg-gradient-to-br from-[var(--primary)] to-[var(--at-ai)] text-xl font-bold text-white">
                OZ
              </span>
              <div>
                <p className="text-lg font-semibold">Obaidullah Zeeshan</p>
                <p className="text-sm text-[var(--muted-foreground)]">
                  AI &amp; Full-Stack Software Engineer
                </p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 md:border-l md:border-[var(--border)] md:pl-6">
              <Stat icon={Clock} label="Response time" value="~1-2 days" />
              <Stat icon={MapPin} label="Working style" value="Remote - any TZ" />
              <Stat icon={Brain} label="Current focus" value="AI-native products" />
            </div>
          </Card>
        </Reveal>

        {/* Ways to connect */}
        <section className="mt-14" aria-labelledby="connect-ways">
          <Reveal>
            <SectionHeading
              eyebrow="Ways to connect"
              title="Pick whatever's easiest"
              sub="Every channel reaches me - choose the one that fits."
            />
          </Reveal>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CHANNELS.map((c, i) => {
              const Icon = c.icon;
              const inner = (
                <Card className="group h-full p-5 transition-all hover:-translate-y-0.5 hover:shadow-[var(--shadow-at-e2)]">
                  <span className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                    <Icon className="h-5 w-5" />
                  </span>
                  <p className="mt-4 flex items-center gap-1.5 text-base font-semibold">
                    {c.label}
                    <ArrowRight className="h-4 w-4 text-[var(--muted-foreground)] transition-transform group-hover:translate-x-0.5" />
                  </p>
                  <p className="mt-1 text-sm text-[var(--muted-foreground)]">{c.desc}</p>
                </Card>
              );
              return (
                <Reveal key={c.label} delay={(i % 4) * 70}>
                  {c.external ? (
                    <a
                      href={c.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block h-full"
                    >
                      {inner}
                    </a>
                  ) : (
                    <Link href={c.href} className="block h-full">
                      {inner}
                    </Link>
                  )}
                </Reveal>
              );
            })}
          </div>
        </section>

        {/* Feedback + Review */}
        <section className="mt-16" aria-labelledby="feedback-center">
          <Reveal>
            <SectionHeading
              eyebrow="Feedback center"
              title="Tell me what you think"
              sub="Send a message for anything - a bug, a feature idea, or a hello - or leave a quick review."
            />
          </Reveal>
          <div className="mt-6 grid gap-6 lg:grid-cols-2 lg:items-start">
            <Reveal>
              {/* Reuses the production contact form; defaults to a feedback purpose. */}
              <ContactForm defaultPurpose="feedback" />
            </Reveal>
            <Reveal delay={100}>
              <ReviewForm />
            </Reveal>
          </div>
        </section>

        {/* Collaboration */}
        <section className="mt-16" aria-labelledby="collab">
          <Reveal>
            <SectionHeading
              eyebrow="Let's build together"
              title="Where I can help"
              sub="Open to freelance, full-time, research, and open-source collaboration."
            />
          </Reveal>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {COLLAB.map((c, i) => {
              const Icon = c.icon;
              return (
                <Reveal key={c.title} delay={(i % 4) * 70}>
                  <Card className="h-full p-5">
                    <span className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)]/10 text-[var(--primary)]">
                      <Icon className="h-5 w-5" />
                    </span>
                    <p className="mt-4 text-base font-semibold">{c.title}</p>
                    <p className="mt-1 text-sm text-[var(--muted-foreground)]">{c.body}</p>
                  </Card>
                </Reveal>
              );
            })}
          </div>
        </section>

        {/* Developer story */}
        <section className="mt-16" aria-labelledby="story">
          <Reveal>
            <Card className="relative overflow-hidden p-8 md:p-10">
              <span className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-at-lg)] bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                <Handshake className="h-5 w-5" />
              </span>
              <h2 id="story" className="mt-4 text-2xl font-semibold tracking-tight">
                Why I built FitWright
              </h2>
              <p className="mt-3 max-w-2xl text-[var(--muted-foreground)]">
                Job hunting shouldn't mean rewriting your resume by hand for every role. I wanted a
                tool that reshapes your real experience to fit each job - honestly, transparently,
                and without inventing things. FitWright is my take on an AI-native product that
                respects your data and your intelligence: bring your own key, see every change, own
                your work.
              </p>
              <p className="mt-3 max-w-2xl text-[var(--muted-foreground)]">
                It's built in the open and improved continuously. If something feels off or could be
                better, that feedback is a gift - tell me, and it shapes the roadmap.
              </p>
              <p className="mt-5 inline-flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                <ShieldCheck className="h-3.5 w-3.5 text-[var(--at-success)]" /> Privacy-first -
                open source - always improving
              </p>
            </Card>
          </Reveal>
        </section>

        {/* FAQ / contact preferences */}
        <section className="mt-16 pb-24" aria-labelledby="faq">
          <Reveal>
            <SectionHeading eyebrow="Good to know" title="Contact preferences" />
          </Reveal>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {FAQ.map((f, i) => (
              <Reveal key={f.q} delay={(i % 3) * 70}>
                <Card className="h-full p-5">
                  <p className="text-sm font-semibold">{f.q}</p>
                  <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">{f.a}</p>
                </Card>
              </Reveal>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--secondary)] text-[var(--foreground)]">
        <Icon className="h-4 w-4" />
      </span>
      <div>
        <p className="text-[11px] uppercase tracking-wide text-[var(--muted-foreground)]">
          {label}
        </p>
        <p className="text-sm font-medium">{value}</p>
      </div>
    </div>
  );
}
