import { describe, it, expect } from 'vitest';

import {
  SAMPLE_RESUME,
  SAMPLE_RESUME_WITH_PHOTO,
  SAMPLE_AVATAR_URL,
} from '@/lib/resume/sample-resume';
import { RESUME_TEMPLATES } from '@/lib/resume/template-catalog';

describe('gallery sample resume photo variants', () => {
  it('the plain sample has no profile photo (photo-less templates preview clean)', () => {
    const pi = SAMPLE_RESUME.personalInfo as Record<string, unknown>;
    // Either absent, or explicitly not shown - never a visible photo.
    const photo = pi.photo as { show?: boolean } | undefined;
    expect(photo?.show ?? false).toBe(false);
    expect(pi.avatarUrl ?? null).toBeNull();
  });

  it('the photo variant enables a bundled placeholder headshot', () => {
    const pi = SAMPLE_RESUME_WITH_PHOTO.personalInfo as Record<string, unknown>;
    const photo = pi.photo as { show: boolean; position: string };
    expect(photo.show).toBe(true);
    // Lets each template place it in its own default slot.
    expect(photo.position).toBe('template-default');
    expect(pi.avatarUrl).toBe(SAMPLE_AVATAR_URL);
    expect(SAMPLE_AVATAR_URL).toBe('/sample-avatar.svg');
  });

  it('keeps all non-photo content identical between the two variants', () => {
    // Only the photo/avatar differ; the rest of the resume is the same fixture.
    expect(SAMPLE_RESUME_WITH_PHOTO.summary).toBe(SAMPLE_RESUME.summary);
    expect(SAMPLE_RESUME_WITH_PHOTO.workExperience).toEqual(SAMPLE_RESUME.workExperience);
    expect(SAMPLE_RESUME_WITH_PHOTO.personalInfo.name).toBe(SAMPLE_RESUME.personalInfo.name);
  });

  it('the catalog has both photo-capable and photo-less templates to exercise both paths', () => {
    expect(RESUME_TEMPLATES.some((t) => t.photoSupport !== 'none')).toBe(true);
    expect(RESUME_TEMPLATES.some((t) => t.photoSupport === 'none')).toBe(true);
  });
});
