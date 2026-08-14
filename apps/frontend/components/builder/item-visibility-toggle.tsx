'use client';

import React from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Button } from '@/components/atelier/button';
import { useTranslations } from '@/lib/i18n';

interface ItemVisibilityToggleProps {
  hidden?: boolean;
  onToggle: () => void;
  className?: string;
}

/**
 * Leave one entry out of the rendered resume without deleting it.
 *
 * Tailoring is mostly deciding what to omit: for this application you want two
 * of your five jobs, and deleting the other three to achieve that would lose
 * them for every future application. Section-level visibility already existed
 * (SectionHeader); this is the same idea one level down, on the individual
 * entry, which is the level a job application actually cares about.
 *
 * Unlike the delete button in these forms, this is NOT revealed on hover: it
 * carries state, and an always-hidden control would conceal the fact that an
 * entry is missing from the document.
 */
export const ItemVisibilityToggle: React.FC<ItemVisibilityToggleProps> = ({
  hidden,
  onToggle,
  className,
}) => {
  const { t } = useTranslations();
  const label = hidden ? t('builder.item.show') : t('builder.item.hide');
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={onToggle}
      aria-pressed={!!hidden}
      aria-label={label}
      title={label}
      className={`${
        hidden ? 'text-[var(--at-warning)]' : 'text-[var(--muted-foreground)]'
      } hover:text-[var(--foreground)] ${className ?? ''}`}
    >
      {hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </Button>
  );
};
