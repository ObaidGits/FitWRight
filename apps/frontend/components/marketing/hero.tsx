'use client';

/**
 * Landing hero (homepage enhancement). The strongest section: value prop +
 * CTAs on the left, a live-looking product mockup with floating AI cards and
 * soft animated gradient blobs on the right. Decorative layers are
 * aria-hidden; motion collapses under prefers-reduced-motion.
 */
import * as React from 'react';
import Link from 'next/link';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import GitBranch from 'lucide-react/dist/esm/icons/git-branch';
import KeyRound from 'lucide-react/dist/esm/icons/key-round';
import { Button } from '@/components/atelier/button';
import { TailorMock, AiSuggestionCard, CoverLetterMock } from '@/components/marketing/mockups';

export function Hero() {
  return (
    <section className="relative overflow-hidden">
      {/* Decorative backdrop */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 at-grid-bg opacity-60" />
        <div
          className="at-blob left-[-6rem] top-[-4rem] h-72 w-72"
          style={{ background: 'var(--at-ai)' }}
        />
        <div
          className="at-blob right-[-4rem] top-[6rem] h-64 w-64"
          style={{ background: 'var(--primary)', animationDelay: '-6s' }}
        />
      </div>

      <div className="relative mx-auto grid w-full max-w-6xl items-center gap-12 px-4 py-20 md:px-8 md:py-28 lg:grid-cols-2">
        {/* Copy */}
        <div className="text-center lg:text-left">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--at-ai-surface)] px-3 py-1 text-xs font-medium text-[var(--at-ai)]">
            <Sparkles className="h-3.5 w-3.5" /> AI-native resume tailoring
          </span>
          <h1 className="mx-auto mt-6 max-w-xl text-4xl font-semibold leading-[1.05] tracking-tight md:text-6xl lg:mx-0">
            Every job is different.
            <br />
            <span className="bg-gradient-to-r from-[var(--primary)] to-[var(--at-ai)] bg-clip-text text-transparent">
              Your resume should be too.
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-lg text-lg text-[var(--muted-foreground)] lg:mx-0">
            FitWright reshapes your resume for each role in seconds — with an honest match score,
            cover letters, interview prep, and a tracker to run your whole search.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3 lg:justify-start">
            <Button asChild size="lg">
              <Link href="/home">
                <Sparkles className="h-4 w-4" /> Start tailoring
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <a
                href="https://github.com/ObaidGits/FitWRight"
                target="_blank"
                rel="noopener noreferrer"
              >
                <GitBranch className="h-4 w-4" /> Star on GitHub
              </a>
            </Button>
          </div>
          {/* Honest trust signals — capabilities, not fabricated social proof. */}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-xs text-[var(--muted-foreground)] lg:justify-start">
            <span className="inline-flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 text-[var(--at-success)]" /> Never fabricates
              experience
            </span>
            <span className="inline-flex items-center gap-1.5">
              <KeyRound className="h-3.5 w-3.5 text-[var(--primary)]" /> Bring your own API key
            </span>
            <span className="inline-flex items-center gap-1.5">
              <GitBranch className="h-3.5 w-3.5 text-[var(--at-ai)]" /> Open source
            </span>
          </div>
        </div>

        {/* Product mockup */}
        <div className="relative mx-auto flex w-full max-w-md items-center justify-center lg:max-w-none">
          <div className="at-float w-full">
            <TailorMock />
          </div>
          <AiSuggestionCard className="at-float-sm absolute -right-3 -top-6 hidden sm:block" />
          <CoverLetterMock className="at-float absolute -bottom-8 -left-4 hidden sm:block" />
        </div>
      </div>
    </section>
  );
}
