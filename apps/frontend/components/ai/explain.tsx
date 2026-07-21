'use client';

/**
 * Explain affordance (Task 10.3 / Req 27.2, 27.4).
 *
 * A small, consistent "explain" trigger that reveals a plain-language
 * explanation of an AI-derived value (e.g. the ATS score, a proposed change).
 * It is COST-FREE: explanations are static, client-side copy - no AI call
 * fires, so nothing is unsolicited or billable. This keeps the AI voice
 * consistent and builds trust without extra spend.
 */
import * as React from 'react';
import CircleHelp from 'lucide-react/dist/esm/icons/circle-help';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/atelier/misc';
import { cn } from '@/lib/utils';

interface ExplainProps {
  /** Plain-language explanation shown on hover/focus. */
  children: React.ReactNode;
  label?: string;
  className?: string;
}

export function Explain({ children, label = 'Explain', className }: ExplainProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            'inline-flex h-5 w-5 items-center justify-center rounded-full text-[var(--muted-foreground)]',
            'hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]',
            className
          )}
        >
          <CircleHelp className="h-3.5 w-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-left leading-relaxed">{children}</TooltipContent>
    </Tooltip>
  );
}
