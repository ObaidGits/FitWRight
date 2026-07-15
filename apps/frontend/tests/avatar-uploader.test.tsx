import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Mock the profile API so no network happens.
vi.mock('@/lib/api/profile', () => ({
  uploadAvatar: vi.fn(async () => ({
    avatar_url: 'https://cdn/x.webp',
    width: 200,
    height: 200,
    dominant_color: '#abcdef',
    deduplicated: false,
  })),
  deleteAvatar: vi.fn(async () => ({ avatar_url: null })),
}));

import { AvatarUploader } from '@/components/profile/avatar-uploader';
import { uploadAvatar, deleteAvatar } from '@/lib/api/profile';

describe('AvatarUploader (shared single upload experience)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows Upload when empty and Replace/Remove when a photo exists', () => {
    const { rerender } = render(
      <AvatarUploader avatarUrl={null} onUploaded={() => {}} onRemoved={() => {}} />
    );
    expect(screen.getByRole('button', { name: /^Upload$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull();

    rerender(
      <AvatarUploader avatarUrl="https://cdn/x.webp" onUploaded={() => {}} onRemoved={() => {}} />
    );
    expect(screen.getByRole('button', { name: /^Replace$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Remove$/ })).toBeInTheDocument();
  });

  it('uploads a selected file and reports the result', async () => {
    const onUploaded = vi.fn();
    const { container } = render(
      <AvatarUploader avatarUrl={null} onUploaded={onUploaded} onRemoved={() => {}} />
    );
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], 'p.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(uploadAvatar).toHaveBeenCalledWith(file));
    await waitFor(() =>
      expect(onUploaded).toHaveBeenCalledWith(
        expect.objectContaining({ avatar_url: 'https://cdn/x.webp' })
      )
    );
  });

  it('rejects oversized files client-side without calling the API', async () => {
    const onError = vi.fn();
    const { container } = render(
      <AvatarUploader
        avatarUrl={null}
        onUploaded={() => {}}
        onRemoved={() => {}}
        onError={onError}
      />
    );
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    // 6 MB > 5 MB cap.
    const big = new File([new Uint8Array(6 * 1024 * 1024)], 'big.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [big] } });
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(uploadAvatar).not.toHaveBeenCalled();
  });

  it('removes the photo', async () => {
    const onRemoved = vi.fn();
    render(
      <AvatarUploader avatarUrl="https://cdn/x.webp" onUploaded={() => {}} onRemoved={onRemoved} />
    );
    fireEvent.click(screen.getByRole('button', { name: /^Remove$/ }));
    await waitFor(() => expect(deleteAvatar).toHaveBeenCalled());
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
  });

  it('the trigger is keyboard-activatable', () => {
    render(<AvatarUploader avatarUrl={null} onUploaded={() => {}} onRemoved={() => {}} />);
    const zone = screen.getByRole('button', { name: /Upload profile photo/i });
    expect(zone).toHaveAttribute('tabindex', '0');
  });
});
