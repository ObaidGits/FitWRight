/**
 * Home "let's work together" CTA (marketing). A personal, conversion-focused
 * section that introduces the developer and guides visitors to /contact -
 * distinct from the product "Get started" CTA above it. Placed right where the
 * reader has just finished the product story, so the natural next question
 * ("who built this, and can I work with them?") is answered with a clear path
 * to reach out.
 *
 * Server component (static markup) wrapped in the existing <Reveal> for the
 * same scroll-in motion as the rest of the homepage; no extra client JS, no CLS.
 * Uses only atelier tokens + existing decorative classes for a seamless match.
 */
import Link from 'next/link';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import Check from 'lucide-react/dist/esm/icons/check';
import MessageSquare from 'lucide-react/dist/esm/icons/message-square';
import Github from 'lucide-react/dist/esm/icons/github';
import Linkedin from 'lucide-react/dist/esm/icons/linkedin';
import Globe from 'lucide-react/dist/esm/icons/globe';

import { Button } from '@/components/atelier/button';
import { Reveal } from '@/components/marketing/reveal';

const TRUST = [
  'Usually replies within a day',
  'Open to collaborations & roles',
  'AI & full-stack engineering',
  'Open-source contributor',
];

const SOCIALS = [
  { icon: Linkedin, label: 'LinkedIn', href: 'https://www.linkedin.com/in/obaidullah-zeeshan/' },
  { icon: Github, label: 'GitHub', href: 'https://github.com/ObaidGits' },
  { icon: Globe, label: 'Portfolio', href: 'https://obaidullah-zeeshan.dev' },
];

export function ContactCta() {
  return (
    <section
      aria-labelledby="contact-cta-heading"
      className="mx-auto w-full max-w-6xl px-4 pb-24 md:px-8"
    >
      <Reveal>
        <div className="relative overflow-hidden rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-at-e2)]">
          {/* Decorative lighting - aria-hidden; motion collapses under reduced-motion. */}
          <div aria-hidden className="pointer-events-none absolute inset-0">
            <div className="absolute inset-0 at-grid-bg opacity-40" />
            <div
              className="at-blob left-[-4rem] top-[-3rem] h-56 w-56"
              style={{ background: 'var(--at-ai)' }}
            />
            <div
              className="at-blob bottom-[-4rem] right-[-3rem] h-56 w-56"
              style={{ background: 'var(--primary)', animationDelay: '-9s' }}
            />
          </div>

          <div className="relative grid gap-10 p-8 md:p-12 lg:grid-cols-[1.1fr_1fr] lg:items-center lg:gap-14">
            {/* Left - invitation + trust */}
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--at-ai)]">
                Let's work together
              </span>
              <h2
                id="contact-cta-heading"
                className="mt-3 text-3xl font-semibold leading-[1.1] tracking-tight md:text-4xl"
              >
                Have an idea worth building?
                <br />
                <span className="bg-gradient-to-r from-[var(--primary)] to-[var(--at-ai)] bg-clip-text text-transparent">
                  I'd love to hear it.
                </span>
              </h2>
              <p className="mt-4 max-w-md text-[var(--muted-foreground)]">
                FitWright is built by{' '}
                <span className="font-medium text-[var(--foreground)]">Obaidullah Zeeshan</span>, a
                full-stack &amp; backend-focused software engineer. Hiring, exploring a
                collaboration, or just want to talk shop about AI and software? My inbox is open -
                and I actually reply.
              </p>

              <ul className="mt-6 grid gap-2.5 sm:grid-cols-2">
                {TRUST.map((label) => (
                  <li key={label} className="flex items-center gap-2.5 text-sm">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--at-success)]/15 text-[var(--at-success)]">
                      <Check className="h-3.5 w-3.5" />
                    </span>
                    <span className="text-[var(--foreground)]">{label}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Right - the CTA card */}
            <div className="rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--at-surface-2)]/70 p-6 shadow-[var(--shadow-at-e1)] backdrop-blur-sm md:p-7">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--at-success)]/40 bg-[var(--at-success)]/10 px-3 py-1 text-xs font-medium text-[var(--at-success)]">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-[var(--at-success)] opacity-70 motion-safe:animate-ping" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--at-success)]" />
                </span>
                Available for new work
              </span>

              <h3 className="mt-4 flex items-center gap-2 text-lg font-semibold">
                <MessageSquare className="h-5 w-5 text-[var(--at-ai)]" /> Start a conversation
              </h3>
              <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">
                Tell me what you're working on. Thoughtful messages get thoughtful replies -
                typically within a day.
              </p>

              <div className="mt-5 flex flex-col gap-2.5 sm:flex-row">
                <Button asChild size="lg" className="sm:flex-1">
                  <Link href="/contact">
                    <MessageSquare className="h-4 w-4" /> Contact me
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </Button>
                <Button asChild size="lg" variant="outline" className="sm:flex-1">
                  <a href="https://github.com/ObaidGits" target="_blank" rel="noopener noreferrer">
                    <Github className="h-4 w-4" /> View GitHub
                  </a>
                </Button>
              </div>

              <div className="mt-5 flex items-center gap-4 border-t border-[var(--border)] pt-4 text-sm text-[var(--muted-foreground)]">
                {SOCIALS.map((s) => {
                  const Icon = s.icon;
                  return (
                    <a
                      key={s.label}
                      href={s.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 transition-colors hover:text-[var(--foreground)]"
                    >
                      <Icon className="h-4 w-4" /> {s.label}
                    </a>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
