import { afterEach, describe, expect, it, vi } from 'vitest';
import * as React from 'react';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';

import { CustomSectionsEditor } from '@/components/resume/custom-sections-editor';
import { DEFAULT_SECTION_META } from '@/lib/utils/section-helpers';
import type { SectionMeta, CustomSection } from '@/components/dashboard/resume-component';

/**
 * CustomSectionsEditor — section ordering/visibility + custom sections in the
 * atelier editor. A stateful harness mirrors the editor's ownership so we can
 * observe cumulative behaviour (add, reorder, hide, edit, delete).
 */
function Harness({
  initialMeta = DEFAULT_SECTION_META,
  initialCustom = {},
  onEmit,
}: {
  initialMeta?: SectionMeta[];
  initialCustom?: Record<string, CustomSection>;
  onEmit?: (n: {
    sectionMeta: SectionMeta[];
    customSections: Record<string, CustomSection>;
  }) => void;
}) {
  const [meta, setMeta] = React.useState(initialMeta);
  const [custom, setCustom] = React.useState(initialCustom);
  return (
    <CustomSectionsEditor
      sectionMeta={meta}
      customSections={custom}
      onChange={(n) => {
        setMeta(n.sectionMeta);
        setCustom(n.customSections);
        onEmit?.(n);
      }}
    />
  );
}

afterEach(() => vi.clearAllMocks());

describe('CustomSectionsEditor', () => {
  it('lists default sections and pins personalInfo (no hide/move controls)', () => {
    render(<Harness />);
    expect(screen.getByText('Summary')).toBeInTheDocument();
    expect(screen.getByText('Experience')).toBeInTheDocument();
    // personalInfo is pinned: no hide toggle for it.
    expect(screen.queryByLabelText(/hide personal info/i)).not.toBeInTheDocument();
  });

  it('adds a custom section via the dialog and shows its content editor', async () => {
    const onEmit = vi.fn();
    render(<Harness onEmit={onEmit} />);

    fireEvent.click(screen.getByRole('button', { name: /add section/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Section name'), {
      target: { value: 'Certifications' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /add section/i }));

    await waitFor(() =>
      expect(onEmit).toHaveBeenCalledWith(
        expect.objectContaining({
          sectionMeta: expect.arrayContaining([
            expect.objectContaining({ displayName: 'Certifications', isDefault: false }),
          ]),
        })
      )
    );
    // A rename input for the new custom section is now rendered.
    expect(screen.getByDisplayValue('Certifications')).toBeInTheDocument();
  });

  it('hides a section (toggles the accessible label)', () => {
    render(<Harness />);
    const hideSummary = screen.getByLabelText('Hide Summary');
    fireEvent.click(hideSummary);
    expect(screen.getByLabelText('Show Summary')).toBeInTheDocument();
  });

  it('reorders sections and reassigns order', () => {
    const onEmit = vi.fn();
    render(<Harness onEmit={onEmit} />);
    // Move "Education" up (swaps with Experience).
    fireEvent.click(screen.getByLabelText('Move Education up'));
    const emitted = onEmit.mock.calls[0][0].sectionMeta as SectionMeta[];
    const byId = Object.fromEntries(emitted.map((s) => [s.id, s.order]));
    expect(byId.education).toBeLessThan(byId.workExperience);
  });

  it('edits custom text content and deletes a custom section', () => {
    const onEmit = vi.fn();
    const meta: SectionMeta[] = [
      ...DEFAULT_SECTION_META,
      {
        id: 'custom_1',
        key: 'custom_1',
        displayName: 'Statement',
        sectionType: 'text',
        isDefault: false,
        isVisible: true,
        order: 6,
      },
    ];
    render(
      <Harness
        initialMeta={meta}
        initialCustom={{ custom_1: { sectionType: 'text', text: '' } }}
        onEmit={onEmit}
      />
    );

    fireEvent.change(screen.getByPlaceholderText(/write the statement content/i), {
      target: { value: 'My professional statement.' },
    });
    expect(onEmit).toHaveBeenCalledWith(
      expect.objectContaining({
        customSections: { custom_1: { sectionType: 'text', text: 'My professional statement.' } },
      })
    );

    fireEvent.click(screen.getByLabelText('Delete Statement'));
    // After deletion the content editor for it is gone.
    expect(screen.queryByDisplayValue('Statement')).not.toBeInTheDocument();
  });
});
