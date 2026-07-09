'use client';

/**
 * Shared template renderer — maps a TemplateType to its render component and
 * wraps it in the light-pinned `.resume-scope` + `.resume-body` (engine
 * isolation, Task 1.3). Reused by the Resume Editor preview and any other
 * on-screen preview so output matches the PDF.
 */
import * as React from 'react';
import type { ResumeData } from '@/components/dashboard/resume-component';
import {
  type TemplateSettings,
  settingsToCssVars,
  DEFAULT_TEMPLATE_SETTINGS,
} from '@/lib/types/template-settings';
import {
  ResumeSingleColumn,
  ResumeTwoColumn,
  ResumeModern,
  ResumeModernTwoColumn,
  ResumeLatex,
  ResumeClean,
  ResumeVivid,
} from '@/components/resume';

export function RenderTemplate({
  data,
  settings = DEFAULT_TEMPLATE_SETTINGS,
}: {
  data: ResumeData;
  settings?: TemplateSettings;
}) {
  const showIcons = settings.showContactIcons;
  const el = (() => {
    switch (settings.template) {
      case 'swiss-two-column':
        return <ResumeTwoColumn data={data} showContactIcons={showIcons} />;
      case 'modern':
        return <ResumeModern data={data} showContactIcons={showIcons} />;
      case 'modern-two-column':
        return <ResumeModernTwoColumn data={data} showContactIcons={showIcons} />;
      case 'latex':
        return <ResumeLatex data={data} showContactIcons={showIcons} />;
      case 'clean':
        return <ResumeClean data={data} showContactIcons={showIcons} />;
      case 'vivid':
        return <ResumeVivid data={data} showContactIcons={showIcons} />;
      case 'swiss-single':
      default:
        return <ResumeSingleColumn data={data} showContactIcons={showIcons} />;
    }
  })();

  return (
    <div className="resume-scope">
      <div className="resume-body bg-white text-black" style={settingsToCssVars(settings)}>
        {el}
      </div>
    </div>
  );
}
