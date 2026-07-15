import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * ProfileSearch (final vertical): debounced query → highlighted results;
 * choosing a result navigates to its section.
 */

const searchProfileMock = vi.fn();
vi.mock('@/lib/api/professional-profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, searchProfile: (...a: unknown[]) => searchProfileMock(...a) };
});

import { ProfileSearch } from '@/components/profile/profile-search';

afterEach(() => vi.clearAllMocks());

describe('ProfileSearch', () => {
  it('queries, highlights, and navigates on choose', async () => {
    searchProfileMock.mockResolvedValue([
      {
        type: 'skill',
        uid: 's1',
        section: 'skills',
        title: '[[Python]]',
        subtitle: 'technical',
        snippet: '',
        score: 5,
      },
    ]);
    const onNavigate = vi.fn();
    render(<ProfileSearch onNavigate={onNavigate} />);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'python' } });

    const option = await screen.findByRole('option');
    // Highlight sentinels are rendered as <mark>, not literal brackets.
    expect(option).toHaveTextContent('Python');
    expect(option.textContent).not.toContain('[[');

    fireEvent.mouseDown(option.querySelector('button')!);
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('skills'));
  });

  it('does not query for very short input', async () => {
    render(<ProfileSearch onNavigate={vi.fn()} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'p' } });
    await new Promise((r) => setTimeout(r, 250));
    expect(searchProfileMock).not.toHaveBeenCalled();
  });
});
