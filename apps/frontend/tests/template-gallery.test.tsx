import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// The gallery renders one live ResumeDocument thumbnail per card. That engine is
// covered by its own test (resume-document.test.tsx); here we stub it so the
// gallery's own behaviour (filter/search/select) is what's exercised - and so 26
// full document renders don't dominate the suite.
vi.mock('@/components/resume/resume-document', () => ({
  ResumeDocument: () => <div data-testid="doc-stub" />,
}));

import { TemplateGallery } from '@/components/resume/template-gallery';
import {
  getPreferredTemplateId,
  setPreferredTemplateId,
  getPreferredTemplateSettings,
} from '@/lib/resume/preferred-template';
import { DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';

afterEach(() => {
  vi.clearAllMocks();
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe('TemplateGallery', () => {
  it('renders template cards and reports a count', () => {
    render(<TemplateGallery />);
    // The default (ATS Classic) is present.
    expect(screen.getAllByText('ATS Classic').length).toBeGreaterThan(0);
    // Count status is shown.
    expect(screen.getByRole('status').textContent).toMatch(/templates?/i);
  });

  it('filters to no-photo templates when toggled', () => {
    render(<TemplateGallery />);
    const before = screen.getByRole('status').textContent ?? '';
    fireEvent.click(screen.getByRole('button', { name: 'No photo' }));
    const after = screen.getByRole('status').textContent ?? '';
    // Filtering changes the count (there are photo-supporting templates).
    expect(after).not.toBe(before);
  });

  it('searches by free text (role / keyword)', () => {
    render(<TemplateGallery />);
    fireEvent.change(screen.getByLabelText('Search templates'), {
      target: { value: 'designer' },
    });
    expect(screen.getAllByText('Designer').length).toBeGreaterThan(0);
    // An unrelated ATS template is filtered out of the grid.
    expect(screen.queryByText('Finance & Banking')).not.toBeInTheDocument();
  });

  it('invokes onSelect with the chosen template', () => {
    const onSelect = vi.fn();
    render(<TemplateGallery onSelect={onSelect} ctaLabel="Use this template" />);
    // Click the first card's "Use this template" button.
    const buttons = screen.getAllByRole('button', { name: 'Use this template' });
    fireEvent.click(buttons[0]);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toHaveProperty('id');
  });
});

describe('preferred-template bridge', () => {
  it('persists and resolves the preferred template settings', () => {
    expect(getPreferredTemplateId()).toBeNull();
    // Unknown / unset -> defaults.
    expect(getPreferredTemplateSettings()).toEqual(DEFAULT_TEMPLATE_SETTINGS);

    setPreferredTemplateId('finance-banking');
    expect(getPreferredTemplateId()).toBe('finance-banking');
    const settings = getPreferredTemplateSettings();
    // finance-banking uses the latex engine.
    expect(settings.template).toBe('latex');
  });

  it('falls back to defaults for an unknown id', () => {
    setPreferredTemplateId('does-not-exist');
    expect(getPreferredTemplateSettings()).toEqual(DEFAULT_TEMPLATE_SETTINGS);
  });
});
