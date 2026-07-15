import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ResumeDocument } from '@/components/resume/resume-document';
import type { ResumeData } from '@/components/dashboard/resume-component';
import { DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';

const data: ResumeData = {
  personalInfo: { name: 'Ada Lovelace', title: 'Engineer', email: 'ada@example.com' },
  summary: 'Pioneering computer scientist.',
  workExperience: [
    {
      id: 1,
      title: 'Analyst',
      company: 'Analytical Engine Co',
      years: '1840 - 1852',
      description: ['Wrote the first algorithm'],
    },
  ],
  education: [],
  personalProjects: [],
  additional: { technicalSkills: ['Mathematics'] },
  customSections: {},
  sectionMeta: [],
};

describe('ResumeDocument — WYSIWYG page surface', () => {
  it('renders the unified resume content on a real page surface', () => {
    render(<ResumeDocument data={data} settings={DEFAULT_TEMPLATE_SETTINGS} />);
    // The canonical renderer output is present (name shows on the page).
    expect(screen.getAllByText('Ada Lovelace').length).toBeGreaterThan(0);
    // At least one A4 page surface is rendered (labelled for a11y).
    const pages = screen.getAllByTestId('resume-page');
    expect(pages.length).toBeGreaterThanOrEqual(1);
    expect(pages[0]).toHaveAttribute('aria-label', expect.stringContaining('Page 1'));
  });

  it('renders a Letter document without error', () => {
    render(
      <ResumeDocument data={data} settings={{ ...DEFAULT_TEMPLATE_SETTINGS, pageSize: 'LETTER' }} />
    );
    expect(screen.getByTestId('resume-document')).toBeInTheDocument();
  });
});
