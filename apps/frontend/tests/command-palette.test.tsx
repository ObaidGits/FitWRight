import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Command palette keyboard navigation (premium ⌘K parity):
 * - ⌘K toggles open, ↑/↓ move the active option (roving aria-selected),
 *   Enter runs the active command, and recently-used destinations lead the
 *   list when the query is empty.
 * The router + search APIs are mocked so nothing hits the network.
 */

const pushMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

vi.mock('@/lib/api/resume', () => ({ fetchResumeList: vi.fn().mockResolvedValue([]) }));
vi.mock('@/lib/api/tracker', () => ({
  listApplications: vi.fn().mockResolvedValue({ columns: {} }),
}));
vi.mock('@/lib/api/search', () => ({
  searchLocal: vi.fn().mockReturnValue([]),
  searchServer: vi.fn().mockResolvedValue({ results: [] }),
  addRecentSearch: vi.fn(),
}));

import { CommandPaletteProvider } from '@/components/command/command-palette';

function renderPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CommandPaletteProvider>
        <div>app</div>
      </CommandPaletteProvider>
    </QueryClientProvider>
  );
}

function openPalette() {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
  });
}

describe('CommandPalette keyboard navigation', () => {
  beforeEach(() => {
    pushMock.mockReset();
    window.localStorage.clear();
  });
  afterEach(() => vi.clearAllMocks());

  it('opens on ⌘K and marks the first option active', () => {
    renderPalette();
    openPalette();
    const input = screen.getByRole('combobox', { name: /command palette/i });
    expect(input).toBeTruthy();
    const options = screen.getAllByRole('option');
    expect(options.length).toBeGreaterThan(1);
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    expect(options[1].getAttribute('aria-selected')).toBe('false');
  });

  it('moves the active option with ArrowDown/ArrowUp (roving selection)', () => {
    renderPalette();
    openPalette();
    const input = screen.getByRole('combobox', { name: /command palette/i });

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    let options = screen.getAllByRole('option');
    expect(options[0].getAttribute('aria-selected')).toBe('false');
    expect(options[1].getAttribute('aria-selected')).toBe('true');
    // aria-activedescendant tracks the active option for screen readers.
    expect(input.getAttribute('aria-activedescendant')).toBe(options[1].id);

    fireEvent.keyDown(input, { key: 'ArrowUp' });
    options = screen.getAllByRole('option');
    expect(options[0].getAttribute('aria-selected')).toBe('true');
  });

  it('runs the active command on Enter and navigates', () => {
    renderPalette();
    openPalette();
    const input = screen.getByRole('combobox', { name: /command palette/i });
    // First option is "Go to Home" (→ /home).
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(pushMock).toHaveBeenCalledWith('/home');
  });

  it('surfaces recently-used destinations first when the query is empty', () => {
    // Seed a recent entry; it should lead the list on next open.
    window.localStorage.setItem(
      'fitwright-command-recents',
      JSON.stringify([{ id: 'nav-settings', label: 'Go to Settings', href: '/settings' }])
    );
    renderPalette();
    openPalette();
    const options = screen.getAllByRole('option');
    expect(options[0].textContent).toContain('Go to Settings');
    expect(options[0].textContent).toContain('Recent');
  });
});
