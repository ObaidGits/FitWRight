'use client';

/** Landing FAQ accordion (homepage). Accessible disclosure, Atelier-styled. */
import * as React from 'react';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';
import { cn } from '@/lib/utils';

const FAQS: { q: string; a: string }[] = [
  {
    q: 'Why use FitWright?',
    a: 'A single master resume rarely fits every role. FitWright reshapes yours for each job description — highlighting the relevant experience and keywords — so you spend minutes, not hours, per application.',
  },
  {
    q: 'Which AI providers are supported?',
    a: 'OpenAI, Anthropic, Google Gemini, OpenRouter, DeepSeek, Groq, and any OpenAI-compatible server. You can also run fully local models with Ollama — no cloud, no cost.',
  },
  {
    q: 'Do you store my data or API key?',
    a: 'You bring your own API key, and it is encrypted at rest on your own instance. Your resume content stays in your local database. FitWright never sends your data to a third party beyond the AI provider you choose.',
  },
  {
    q: 'Can I export polished PDFs?',
    a: 'Yes. Every resume and cover letter exports to a clean, ATS-friendly PDF using multiple templates with a live preview that matches the output exactly.',
  },
  {
    q: 'Can I edit what the AI produces?',
    a: 'Always. Every AI change is shown as a preview with a clear diff — you accept, tweak, or discard. You can also ask AI to rewrite any single bullet, and edit everything by hand.',
  },
  {
    q: 'Does it handle multiple resumes and applications?',
    a: 'Yes. Keep one master resume, generate a tailored variant per job, and track every application from applied to offer on a Kanban board.',
  },
  {
    q: 'Is FitWright open source?',
    a: 'Yes — the full source is on GitHub. You can self-host it, inspect exactly how your data is handled, and contribute.',
  },
];

export function Faq() {
  const [open, setOpen] = React.useState<number | null>(0);
  return (
    <div className="mx-auto max-w-2xl divide-y divide-[var(--border)] rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)]">
      {FAQS.map((item, i) => {
        const isOpen = open === i;
        return (
          <div key={item.q}>
            <button
              onClick={() => setOpen(isOpen ? null : i)}
              aria-expanded={isOpen}
              className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
            >
              <span className="text-sm font-medium text-[var(--foreground)]">{item.q}</span>
              <ChevronDown
                className={cn(
                  'h-4 w-4 shrink-0 text-[var(--muted-foreground)] transition-transform duration-[var(--duration-at-base)]',
                  isOpen && 'rotate-180'
                )}
              />
            </button>
            <div
              className={cn(
                'grid overflow-hidden px-5 transition-all duration-[var(--duration-at-base)] ease-[var(--ease-at-out)]',
                isOpen ? 'grid-rows-[1fr] pb-4' : 'grid-rows-[0fr]'
              )}
            >
              <p className="min-h-0 overflow-hidden text-sm leading-relaxed text-[var(--muted-foreground)]">
                {item.a}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
