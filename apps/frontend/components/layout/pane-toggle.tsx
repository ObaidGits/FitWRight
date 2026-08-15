'use client';

import * as React from 'react';

export type Pane = 'edit' | 'preview';

/**
 * Which of two panes a narrow screen shows.
 *
 * Every two-pane surface in the app (Tailor, Builder, Wizard, resume detail) is
 * `lg:grid-cols-2`, so below `lg` the panes stack and the preview lands beneath
 * the entire form - far below the fold, with nothing telling the user to scroll.
 * On Tailor that made a successful tailoring look like nothing had happened.
 *
 * Hidden from `lg` up, where both panes are on screen together and the control
 * would be meaningless.
 *
 * Shared rather than copied per page: this markup existed in two places already,
 * and the same copy-per-page habit is what left `TabsList` overflowing on the two
 * pages that had not hit the bug yet.
 */
export function PaneToggle({
  value,
  onChange,
  editLabel = 'Edit',
  previewLabel = 'Preview',
  label = 'Show editor or preview',
  className,
}: {
  value: Pane;
  onChange: (pane: Pane) => void;
  editLabel?: string;
  previewLabel?: string;
  /** Accessible name for the group. */
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={`flex shrink-0 gap-1 rounded-[var(--radius-at-lg)] bg-[var(--secondary)] p-1 lg:hidden ${className ?? ''}`}
    >
      {(['edit', 'preview'] as Pane[]).map((pane) => (
        <button
          key={pane}
          type="button"
          onClick={() => onChange(pane)}
          aria-pressed={value === pane}
          className={`rounded-[var(--radius-at-md)] px-3 py-1.5 text-sm font-medium transition-colors ${
            value === pane
              ? 'bg-[var(--card)] text-[var(--foreground)] shadow-[var(--shadow-at-e1)]'
              : 'text-[var(--muted-foreground)]'
          }`}
        >
          {pane === 'edit' ? editLabel : previewLabel}
        </button>
      ))}
    </div>
  );
}

/** Classes that show a pane only when it is the selected one, below `lg`. */
export function paneVisibility(active: boolean, display: 'block' | 'flex' = 'block'): string {
  return `${active ? display : 'hidden'} lg:${display}`;
}
