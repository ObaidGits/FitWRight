import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

/**
 * The mode-mismatch guard: warns loudly (banner + console) when the frontend's
 * baked NEXT_PUBLIC_SINGLE_USER_MODE disagrees with the backend's
 * SINGLE_USER_MODE, and renders nothing when they agree.
 */

const modeCfg = vi.hoisted(() => ({ singleUser: false }));
vi.mock('@/lib/config/auth', () => ({
  get SINGLE_USER_MODE() {
    return modeCfg.singleUser;
  },
}));

let backendSingleUser: boolean | undefined;
vi.mock('@/features/home/hooks', () => ({
  useSystemStatus: () => ({ data: { single_user: backendSingleUser } }),
}));

import { ModeMismatchBanner } from '@/components/dev/mode-mismatch-banner';

afterEach(() => {
  vi.clearAllMocks();
  modeCfg.singleUser = false;
  backendSingleUser = undefined;
});

describe('ModeMismatchBanner', () => {
  it('renders nothing when frontend and backend modes agree', () => {
    modeCfg.singleUser = false;
    backendSingleUser = false;
    const { container } = render(<ModeMismatchBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing until the backend reports its mode', () => {
    modeCfg.singleUser = true;
    backendSingleUser = undefined; // status not loaded yet
    const { container } = render(<ModeMismatchBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('warns when the UI is hosted but the backend is single-user', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    modeCfg.singleUser = false;
    backendSingleUser = true;
    render(<ModeMismatchBanner />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('warns when the UI is single-user but the backend is hosted, and is dismissible', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    modeCfg.singleUser = true;
    backendSingleUser = false;
    render(<ModeMismatchBanner />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
