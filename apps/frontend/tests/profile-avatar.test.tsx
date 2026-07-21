import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ProfileAvatar } from '@/components/common/profile-avatar';
import { PhotoFrame, resolveResumePhoto } from '@/components/resume/photo-frame';
import { DEFAULT_PHOTO_CONFIG } from '@/lib/types/photo';
import type { ResumeData } from '@/components/dashboard/resume-component';

const CLOUD = 'https://res.cloudinary.com/demo/image/upload/v1/u/abc.webp';

describe('ProfileAvatar (public/SEO)', () => {
  it('renders initials fallback when there is no photo', () => {
    render(<ProfileAvatar name="Ada Lovelace" />);
    expect(screen.getByText('AL')).toBeInTheDocument();
  });

  it('renders a responsive, CLS-safe img with alt + dimensions', () => {
    render(
      <ProfileAvatar
        url={CLOUD}
        srcset={[
          { url: `${CLOUD}?1`, width: 96 },
          { url: `${CLOUD}?2`, width: 192 },
        ]}
        size={80}
        name="Ada Lovelace"
        dominantColor="#123456"
        priority
      />
    );
    const img = screen.getByAltText('Ada Lovelace - profile photo') as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.getAttribute('srcset')).toContain('96w');
    expect(img.getAttribute('srcset')).toContain('192w');
    expect(img.getAttribute('width')).toBe('80');
    expect(img.getAttribute('height')).toBe('80');
    // Above-the-fold hero -> eager load.
    expect(img.getAttribute('loading')).toBe('eager');
  });

  it('lazy-loads when not priority', () => {
    render(<ProfileAvatar url={CLOUD} name="X" />);
    const img = screen.getByAltText('X - profile photo');
    expect(img.getAttribute('loading')).toBe('lazy');
  });

  it('falls back to initials when the image fails to load', () => {
    render(<ProfileAvatar url={CLOUD} name="Ada Lovelace" />);
    const img = screen.getByAltText('Ada Lovelace - profile photo');
    fireEvent.error(img);
    // The broken image is replaced by the initials fallback.
    expect(screen.queryByAltText('Ada Lovelace - profile photo')).toBeNull();
    expect(screen.getByText('AL')).toBeInTheDocument();
  });
});

describe('PublicProfileView wires the responsive avatar', () => {
  it('renders the hero photo with srcset + eager loading', async () => {
    const { PublicProfileView } = await import('@/components/public/public-profile-view');
    const profile = {
      slug: 'ada',
      visibility: 'public' as const,
      identity: {
        name: 'Ada Lovelace',
        headline: 'Engineer',
        avatarUrl: CLOUD,
        avatarSrcset: [
          { url: `${CLOUD}?96`, width: 96 },
          { url: `${CLOUD}?192`, width: 192 },
        ],
        avatarDominantColor: '#112233',
      },
      summary: '',
      experience: [],
      projects: [],
      skills: [],
      education: [],
    };
    render(<PublicProfileView profile={profile} vcardUrl="/v.vcf" theme="minimal" />);
    const img = screen.getByAltText('Ada Lovelace - profile photo');
    expect(img.getAttribute('srcset')).toContain('96w');
    expect(img.getAttribute('loading')).toBe('eager');
  });
});

describe('PhotoFrame (resume render - preview == PDF)', () => {
  const baseData: ResumeData = {
    personalInfo: {
      name: 'Ada',
      avatarUrl: CLOUD,
      photo: { ...DEFAULT_PHOTO_CONFIG, show: true },
    },
  };

  it('eager-loads the resume photo (PDF parity)', () => {
    const photo = resolveResumePhoto(baseData, 'swiss-single')!;
    render(<PhotoFrame url={photo.url} config={photo.config} name={photo.name} />);
    const img = screen.getByAltText('Ada profile photo');
    expect(img.getAttribute('loading')).toBe('eager');
    expect(img.getAttribute('fetchpriority')).toBe('high');
  });

  it('renders nothing for a photo-incapable template (latex)', () => {
    expect(resolveResumePhoto(baseData, 'latex')).toBeNull();
  });

  it('renders nothing when photo is hidden', () => {
    const hidden: ResumeData = {
      personalInfo: {
        name: 'Ada',
        avatarUrl: CLOUD,
        photo: { ...DEFAULT_PHOTO_CONFIG, show: false },
      },
    };
    expect(resolveResumePhoto(hidden, 'swiss-single')).toBeNull();
  });

  it('applies crop -> object-fit and offset -> object-position', () => {
    const cfg = {
      ...DEFAULT_PHOTO_CONFIG,
      show: true,
      crop: 'contain' as const,
      offsetX: 20,
      offsetY: 80,
    };
    render(<PhotoFrame url={CLOUD} config={cfg} name="Ada" />);
    const img = screen.getByAltText('Ada profile photo') as HTMLImageElement;
    expect(img.style.objectFit).toBe('contain');
    expect(img.style.objectPosition).toBe('20% 80%');
  });
});
