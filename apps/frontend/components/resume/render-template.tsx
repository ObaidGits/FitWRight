'use client';

/**
 * Shared on-screen resume renderer.
 *
 * There is ONE resume renderer in the app - the canonical `Resume` component
 * (components/dashboard/resume-component) that the print/PDF path uses. This
 * wrapper delegates to it via {@link ResumeDocument}, which lays the output onto
 * true-to-size A4/Letter page surfaces so the in-app preview is a WYSIWYG match
 * of the exported PDF (identical template mapping, width, margins, wrapping, and
 * visible page breaks). Kept as `RenderTemplate` with the same props so every
 * existing preview call site (resume editor, wizard) is unchanged.
 */
import * as React from 'react';

import type { ResumeData } from '@/components/dashboard/resume-component';
import { type TemplateSettings, DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import { ResumeDocument } from './resume-document';

export function RenderTemplate({
  data,
  settings = DEFAULT_TEMPLATE_SETTINGS,
  className,
}: {
  data: ResumeData;
  settings?: TemplateSettings;
  className?: string;
}) {
  return <ResumeDocument data={data} settings={settings} className={className} />;
}

export default RenderTemplate;
