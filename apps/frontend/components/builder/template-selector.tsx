'use client';

import React from 'react';
import { type TemplateType, TEMPLATE_OPTIONS } from '@/lib/types/template-settings';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';

interface TemplateSelectorProps {
  value: TemplateType;
  onChange: (template: TemplateType) => void;
}

/**
 * Template Selector Component
 *
 * Visual thumbnail buttons for selecting resume templates (Atelier tokens).
 */
export const TemplateSelector: React.FC<TemplateSelectorProps> = ({ value, onChange }) => {
  const { t } = useTranslations();
  const templateLabels = {
    'swiss-single': {
      name: t('builder.formatting.templates.swissSingle.name'),
      description: t('builder.formatting.templates.swissSingle.description'),
    },
    'swiss-two-column': {
      name: t('builder.formatting.templates.swissTwoColumn.name'),
      description: t('builder.formatting.templates.swissTwoColumn.description'),
    },
    modern: {
      name: t('builder.formatting.templates.modern.name'),
      description: t('builder.formatting.templates.modern.description'),
    },
    'modern-two-column': {
      name: t('builder.formatting.templates.modernTwoColumn.name'),
      description: t('builder.formatting.templates.modernTwoColumn.description'),
    },
    latex: {
      name: t('builder.formatting.templates.latex.name'),
      description: t('builder.formatting.templates.latex.description'),
    },
    clean: {
      name: t('builder.formatting.templates.clean.name'),
      description: t('builder.formatting.templates.clean.description'),
    },
    vivid: {
      name: t('builder.formatting.templates.vivid.name'),
      description: t('builder.formatting.templates.vivid.description'),
    },
  };

  return (
    <div className="flex flex-wrap gap-3">
      {TEMPLATE_OPTIONS.map((template) => (
        <button
          key={template.id}
          onClick={() => onChange(template.id)}
          className={cn(
            'group flex flex-col items-center rounded-[var(--radius-at-md)] border p-3 transition-colors',
            value === template.id
              ? 'border-[var(--primary)] bg-[var(--accent)] ring-1 ring-[var(--primary)]'
              : 'border-[var(--border)] bg-[var(--card)] hover:bg-[var(--accent)]'
          )}
          title={templateLabels[template.id].description}
        >
          {/* Template Thumbnail */}
          <div className="mb-2 flex h-20 w-16 items-center justify-center">
            <TemplateThumbnail type={template.id} isActive={value === template.id} />
          </div>

          {/* Template Name */}
          <span
            className={cn(
              'text-[10px] font-semibold uppercase tracking-wide',
              value === template.id ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]'
            )}
          >
            {templateLabels[template.id].name}
          </span>
        </button>
      ))}
    </div>
  );
};

/**
 * Template Thumbnail — visual representation of each template layout.
 * Exported for use in FormattingControls.
 */
interface TemplateThumbnailProps {
  type: TemplateType;
  isActive: boolean;
}

export const TemplateThumbnail: React.FC<TemplateThumbnailProps> = ({ type, isActive }) => {
  const lineColor = isActive ? 'bg-[var(--primary)]' : 'bg-[var(--muted-foreground)]';
  const borderColor = isActive ? 'border-[var(--primary)]' : 'border-[var(--border)]';
  const accentColor = isActive ? 'bg-[var(--primary)]' : 'bg-[var(--primary)]/60';

  if (type === 'swiss-single') {
    return (
      <div
        className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
      >
        <div className={`h-2 ${lineColor} w-full`}></div>
        <div className={`h-0.5 ${lineColor} w-3/4`}></div>
        <div className="mt-1 flex-1 space-y-1">
          <div className={`h-0.5 ${lineColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
          <div className="h-1"></div>
          <div className={`h-0.5 ${lineColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-3/6 opacity-50`}></div>
        </div>
      </div>
    );
  }

  if (type === 'latex') {
    return (
      <div
        className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
      >
        <div className="flex flex-col items-center gap-0.5">
          <div className={`h-1.5 ${lineColor} w-2/3`}></div>
          <div className={`h-0.5 ${lineColor} w-1/2 opacity-60`}></div>
        </div>
        <div className="mt-1 flex-1 space-y-1">
          <div className={`h-0.5 ${lineColor} w-2/5 border-b ${borderColor} pb-1`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
          <div className="h-0.5"></div>
          <div className={`h-0.5 ${lineColor} w-1/3 border-b ${borderColor} pb-1`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
        </div>
      </div>
    );
  }

  if (type === 'clean') {
    return (
      <div
        className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
      >
        <div className="flex flex-col items-center gap-0.5">
          <div className={`h-1.5 ${lineColor} w-1/2 opacity-70`}></div>
          <div className={`h-0.5 ${lineColor} w-2/3 opacity-40`}></div>
        </div>
        <div className="mt-1 flex-1 space-y-1">
          <div className={`h-1 ${lineColor} w-1/2 opacity-30 border-b ${borderColor}`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
          <div className="h-0.5"></div>
          <div className={`h-1 ${lineColor} w-2/5 opacity-30 border-b ${borderColor}`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
        </div>
      </div>
    );
  }

  if (type === 'modern') {
    return (
      <div
        className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
      >
        <div className="flex flex-col items-center gap-0.5">
          <div className={`h-2 ${lineColor} w-3/4`}></div>
          <div className={`h-0.5 ${accentColor} w-1/3`}></div>
        </div>
        <div className="mt-1 flex-1 space-y-1">
          <div className={`h-0.5 ${accentColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
          <div className="h-0.5"></div>
          <div className={`h-0.5 ${accentColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-3/6 opacity-50`}></div>
        </div>
      </div>
    );
  }

  if (type === 'modern-two-column') {
    return (
      <div
        className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
      >
        <div className="flex flex-col items-center gap-0.5">
          <div className={`h-1.5 ${lineColor} w-3/4`}></div>
          <div className={`h-0.5 ${accentColor} w-1/3`}></div>
        </div>
        <div className="mt-1 flex flex-1 gap-1">
          <div className="w-2/3 space-y-0.5">
            <div className={`h-0.5 ${accentColor} w-full`}></div>
            <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
            <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
            <div className="h-0.5"></div>
            <div className={`h-0.5 ${accentColor} w-full`}></div>
            <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          </div>
          <div className={`w-1/3 space-y-0.5 border ${borderColor} pl-1`}>
            <div className={`h-0.5 ${accentColor} w-full`}></div>
            <div className={`h-0.5 ${lineColor} w-4/5 opacity-50`}></div>
            <div className="h-0.5"></div>
            <div className={`h-0.5 ${accentColor} w-full`}></div>
            <div className={`h-0.5 ${lineColor} w-3/5 opacity-50`}></div>
          </div>
        </div>
      </div>
    );
  }

  if (type === 'vivid') {
    return (
      <div
        className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
      >
        <div className="flex items-center gap-0.5">
          <div className={`h-1.5 ${accentColor} w-1/3`}></div>
          <div className={`h-1.5 ${accentColor} w-1/4 opacity-50`}></div>
        </div>
        <div className={`h-0.5 ${lineColor} w-2/3 opacity-40`}></div>
        <div className="mt-0.5 flex flex-1 gap-1">
          <div className="w-2/3 space-y-0.5">
            <div className={`h-0.5 ${accentColor} w-1/2`}></div>
            <div className="flex items-center gap-0.5">
              <div className={`h-0.5 w-0.5 ${accentColor}`}></div>
              <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
            </div>
            <div className="flex items-center gap-0.5">
              <div className={`h-0.5 w-0.5 ${accentColor}`}></div>
              <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
            </div>
            <div className="h-0.5"></div>
            <div className={`h-0.5 ${accentColor} w-2/5`}></div>
          </div>
          <div className="w-1/3 space-y-0.5">
            <div className={`h-0.5 ${accentColor} w-full`}></div>
            <div className={`h-0.5 ${lineColor} w-4/5 opacity-50`}></div>
            <div className="h-0.5"></div>
            <div className={`h-0.5 ${accentColor} w-full`}></div>
            <div className={`h-0.5 ${lineColor} w-3/5 opacity-50`}></div>
          </div>
        </div>
      </div>
    );
  }

  // Two column thumbnail (swiss-two-column)
  return (
    <div
      className={`flex h-18 w-14 flex-col gap-1 rounded-[var(--radius-at-sm)] border ${borderColor} bg-[var(--card)] p-1.5`}
    >
      <div className="flex flex-col items-center gap-0.5">
        <div className={`h-1.5 ${lineColor} w-3/4`}></div>
        <div className={`h-0.5 ${lineColor} w-1/2 opacity-70`}></div>
      </div>
      <div className="mt-1 flex flex-1 gap-1">
        <div className="w-2/3 space-y-0.5">
          <div className={`h-0.5 ${lineColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
          <div className={`h-0.5 ${lineColor} w-4/6 opacity-50`}></div>
          <div className="h-0.5"></div>
          <div className={`h-0.5 ${lineColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-5/6 opacity-50`}></div>
        </div>
        <div className={`w-1/3 space-y-0.5 border-l ${borderColor} pl-1`}>
          <div className={`h-0.5 ${lineColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-4/5 opacity-50`}></div>
          <div className="h-0.5"></div>
          <div className={`h-0.5 ${lineColor} w-full`}></div>
          <div className={`h-0.5 ${lineColor} w-3/5 opacity-50`}></div>
        </div>
      </div>
    </div>
  );
};

export default TemplateSelector;
