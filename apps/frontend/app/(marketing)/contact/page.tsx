/**
 * Contact page (marketing). A premium, trust-building surface - not a bare form.
 * Left: an invitation, live availability, response SLA, what I work on, and
 * direct channels. Right: the production-wired contact form. SEO + JSON-LD +
 * OpenGraph/Twitter are set here (server component); the form is a client island.
 */
import type { Metadata } from 'next';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Clock from 'lucide-react/dist/esm/icons/clock';
import Globe from 'lucide-react/dist/esm/icons/globe';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import Brain from 'lucide-react/dist/esm/icons/brain';
import Layers from 'lucide-react/dist/esm/icons/layers';
import GitBranch from 'lucide-react/dist/esm/icons/git-branch';
import Linkedin from 'lucide-react/dist/esm/icons/linkedin';
import Github from 'lucide-react/dist/esm/icons/github';
import ExternalLink from 'lucide-react/dist/esm/icons/external-link';

import { Reveal } from '@/components/marketing/reveal';
import { ContactForm } from '@/components/contact/contact-form';
import { JsonLd } from '@/lib/seo/json-ld';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { contactPageSchema, breadcrumbSchema } from '@/lib/seo/structured-data';

export const metadata: Metadata = buildMetadata({
  title: "Contact - Let's build something great",
  description:
    'Have an idea, a role, or a collaboration in mind? Reach out about AI engineering, full-stack development, or FitWright. Every message reaches my inbox directly.',
  path: '/contact',
  keywords: KEYWORDS.contact,
  socialTitle: "Contact - Let's build something great - FitWright",
});

const FOCUS = [
  { icon: Brain, label: 'AI & LLM engineering' },
  { icon: Layers, label: 'Full-stack product development' },
  { icon: GitBranch, label: 'Open-source & systems' },
];

const CHANNELS = [
  {
    icon: Linkedin,
    label: 'LinkedIn',
    detail: 'in/obaidullah-zeeshan',
    href: 'https://www.linkedin.com/in/obaidullah-zeeshan/',
  },
  { icon: Github, label: 'GitHub', detail: 'ObaidGits', href: 'https://github.com/ObaidGits' },
  {
    icon: Globe,
    label: 'Portfolio',
    detail: 'obaidullah-zeeshan.dev',
    href: 'https://obaidullah-zeeshan.dev',
  },
];

export default function ContactPage() {
  return (
    <main className="relative overflow-hidden">
      {/* Decorative backdrop (aria-hidden; motion collapses under reduced-motion). */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 at-grid-bg opacity-50" />
        <div
          className="at-blob left-[-6rem] top-[-3rem] h-72 w-72"
          style={{ background: 'var(--at-ai)' }}
        />
        <div
          className="at-blob right-[-5rem] top-[10rem] h-64 w-64"
          style={{ background: 'var(--primary)', animationDelay: '-7s' }}
        />
      </div>

      <JsonLd
        data={[
          contactPageSchema('/contact'),
          breadcrumbSchema([
            { name: 'Home', path: '/' },
            { name: 'Contact', path: '/contact' },
          ]),
        ]}
      />

      <div className="relative mx-auto grid w-full max-w-6xl gap-10 px-4 py-16 md:px-8 md:py-24 lg:grid-cols-[1fr_1.1fr] lg:gap-16">
        {/* Left - invitation + trust */}
        <Reveal className="lg:sticky lg:top-24 lg:self-start">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--at-success)]/40 bg-[var(--at-success)]/10 px-3 py-1 text-xs font-medium text-[var(--at-success)]">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-[var(--at-success)] opacity-70 motion-safe:animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--at-success)]" />
            </span>
            Available for new work &amp; collaboration
          </span>

          <h1 className="mt-6 text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
            Have an idea?
            <br />
            <span className="bg-gradient-to-r from-[var(--primary)] to-[var(--at-ai)] bg-clip-text text-transparent">
              Let's talk.
            </span>
          </h1>
          <p className="mt-5 max-w-md text-lg text-[var(--muted-foreground)]">
            I'm always up for a good conversation about software, AI, engineering, or a role worth
            exploring. Tell me what you're working on - thoughtful messages get thoughtful replies.
          </p>

          {/* SLA + reach */}
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
              <Clock className="h-5 w-5 text-[var(--primary)]" />
              <p className="mt-2 text-sm font-medium">Fast, real replies</p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Usually within 1-2 business days.
              </p>
            </div>
            <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
              <Globe className="h-5 w-5 text-[var(--at-ai)]" />
              <p className="mt-2 text-sm font-medium">Remote-friendly</p>
              <p className="text-xs text-[var(--muted-foreground)]">
                I work comfortably across timezones.
              </p>
            </div>
          </div>

          {/* Focus areas */}
          <div className="mt-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
              What I love to talk about
            </p>
            <ul className="mt-3 space-y-2">
              {FOCUS.map((f) => {
                const Icon = f.icon;
                return (
                  <li key={f.label} className="flex items-center gap-2.5 text-sm">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--at-ai-surface)] text-[var(--at-ai)]">
                      <Icon className="h-4 w-4" />
                    </span>
                    {f.label}
                  </li>
                );
              })}
            </ul>
          </div>

          {/* Direct channels */}
          <div className="mt-8">
            <p className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
              Prefer another channel?
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {CHANNELS.map((c) => {
                const Icon = c.icon;
                return (
                  <a
                    key={c.label}
                    href={c.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] px-3 py-2.5 transition-colors hover:bg-[var(--accent)]"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--secondary)] text-[var(--foreground)]">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="flex-1">
                      <span className="block text-sm font-medium">{c.label}</span>
                      <span className="block text-xs text-[var(--muted-foreground)]">
                        {c.detail}
                      </span>
                    </span>
                    <ExternalLink className="h-4 w-4 text-[var(--muted-foreground)] transition-transform group-hover:translate-x-0.5" />
                  </a>
                );
              })}
            </div>
          </div>

          <p className="mt-6 inline-flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <ShieldCheck className="h-3.5 w-3.5 text-[var(--at-success)]" /> Your details stay
            private - used only to reply.
          </p>
        </Reveal>

        {/* Right - the form */}
        <Reveal delay={120}>
          <div className="mb-4 flex items-center gap-2 lg:hidden">
            <Sparkles className="h-4 w-4 text-[var(--at-ai)]" />
            <span className="text-sm font-medium">Send a message</span>
          </div>
          <ContactForm />
        </Reveal>
      </div>
    </main>
  );
}
