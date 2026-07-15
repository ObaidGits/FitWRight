import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * SkillTagInput (P5 UX):
 * - Renders existing skills as removable chips.
 * - Committing with Enter adds a skill; Backspace on empty removes the last.
 * - Autocomplete queries the backend and clicking a suggestion adds it.
 */

const suggestSkillsMock = vi.fn();
vi.mock('@/lib/api/professional-profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, suggestSkills: (...a: unknown[]) => suggestSkillsMock(...a) };
});

import * as React from 'react';

import { SkillTagInput } from '@/components/profile/skill-tag-input';

function Controlled({ autocomplete = false }: { autocomplete?: boolean }) {
  const [values, setValues] = React.useState<string[]>(['Python']);
  return (
    <SkillTagInput
      id="skills-technical"
      label="Technical skills"
      values={values}
      onChange={setValues}
      autocomplete={autocomplete}
    />
  );
}

afterEach(() => vi.clearAllMocks());

describe('SkillTagInput', () => {
  it('renders chips and adds on Enter', () => {
    render(<Controlled />);
    expect(screen.getByText('Python')).toBeInTheDocument();

    const input = screen.getByRole('combobox');
    fireEvent.change(input, { target: { value: 'React' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(screen.getByText('React')).toBeInTheDocument();
  });

  it('removes the last chip on Backspace when empty', () => {
    render(<Controlled />);
    const input = screen.getByRole('combobox');
    fireEvent.keyDown(input, { key: 'Backspace' });
    expect(screen.queryByText('Python')).not.toBeInTheDocument();
  });

  it('does not add duplicates', () => {
    render(<Controlled />);
    const input = screen.getByRole('combobox');
    fireEvent.change(input, { target: { value: 'python' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    // Still exactly one Python chip.
    expect(screen.getAllByText(/python/i)).toHaveLength(1);
  });

  it('shows autocomplete suggestions and adds on click', async () => {
    suggestSkillsMock.mockResolvedValue([
      { canonical: 'javascript', displayName: 'JavaScript', category: 'technical' },
    ]);
    render(<Controlled autocomplete />);
    const input = screen.getByRole('combobox');
    fireEvent.change(input, { target: { value: 'java' } });

    const option = await screen.findByRole('option');
    expect(option).toHaveTextContent('JavaScript');
    fireEvent.mouseDown(option.querySelector('button')!);

    await waitFor(() => expect(screen.getByText('JavaScript')).toBeInTheDocument());
  });
});
