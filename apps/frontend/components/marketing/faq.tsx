'use client';

/** Landing FAQ accordion (homepage). Accessible disclosure, Atelier-styled. */
import * as React from 'react';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';
import { cn } from '@/lib/utils';
import { LANDING_FAQS } from './faq-data';

export function Faq() {
  const [open, setOpen] = React.useState<number | null>(0);
  return (
    <div className="mx-auto max-w-2xl divide-y divide-[var(--border)] rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)]">
      {LANDING_FAQS.map((item, i) => {
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
