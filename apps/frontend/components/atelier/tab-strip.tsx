'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Atelier TabStrip — a token-based segmented control for the controlled
 * (activeTab / onTabChange) pattern used where panels are rendered outside a
 * Radix Tabs tree (e.g. split editor/preview layouts). Visually matches the
 * Atelier TabsList/TabsTrigger styling.
 */
export interface TabStripItem {
  id: string;
  label: string;
  disabled?: boolean;
}

export interface TabStripProps {
  tabs: TabStripItem[];
  activeTab: string;
  onTabChange: (tabId: string) => void;
  className?: string;
  'aria-label'?: string;
}

export const TabStrip: React.FC<TabStripProps> = ({
  tabs,
  activeTab,
  onTabChange,
  className,
  'aria-label': ariaLabel,
}) => {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        'inline-flex items-center gap-1 rounded-[var(--radius-at-lg)] bg-[var(--secondary)] p-1',
        className
      )}
    >
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={tab.disabled}
            onClick={() => !tab.disabled && onTabChange(tab.id)}
            className={cn(
              'inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-at-md)] px-3 py-1.5 text-sm font-medium',
              'transition-colors duration-[var(--duration-at-base)]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]',
              'disabled:pointer-events-none disabled:opacity-50',
              isActive
                ? 'bg-[var(--card)] text-[var(--foreground)] shadow-[var(--shadow-at-e1)]'
                : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]'
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
};
