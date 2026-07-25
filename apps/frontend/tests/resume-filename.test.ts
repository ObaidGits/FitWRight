import { describe, it, expect } from 'vitest';
import { buildResumeFilename, slugifyNamePart } from '@/lib/resume/filename';

describe('slugifyNamePart', () => {
  it('strips accents and non-alphanumerics, collapsing to underscores', () => {
    expect(slugifyNamePart('Obaïd  Zeeshan!')).toBe('Obaid_Zeeshan');
    expect(slugifyNamePart('  Full / Stack  Dev ')).toBe('Full_Stack_Dev');
  });

  it('caps length and trims trailing underscores', () => {
    expect(slugifyNamePart('a'.repeat(50), 10)).toBe('aaaaaaaaaa');
    expect(slugifyNamePart('name---', 40)).toBe('name');
  });

  it('returns empty for nullish input', () => {
    expect(slugifyNamePart(undefined)).toBe('');
    expect(slugifyNamePart(null)).toBe('');
    expect(slugifyNamePart('')).toBe('');
  });
});

describe('buildResumeFilename', () => {
  it('uses name + company when both present', () => {
    expect(buildResumeFilename({ name: 'Obaid Zeeshan', company: 'Acme' })).toBe(
      'Obaid_Zeeshan_Acme_Resume.pdf'
    );
  });

  it('falls back to role as the tail when there is no company', () => {
    expect(buildResumeFilename({ name: 'Obaid Zeeshan', role: 'Full Stack Dev' })).toBe(
      'Obaid_Zeeshan_Full_Stack_Dev_Resume.pdf'
    );
  });

  it('uses name + kind when only the name is known', () => {
    expect(buildResumeFilename({ name: 'Obaid Zeeshan' })).toBe('Obaid_Zeeshan_Resume.pdf');
  });

  it('labels cover letters distinctly', () => {
    expect(buildResumeFilename({ name: 'Obaid Zeeshan', kind: 'cover-letter' })).toBe(
      'Obaid_Zeeshan_Cover_Letter.pdf'
    );
  });

  it('falls back to a short id-based name when no identity is available', () => {
    expect(buildResumeFilename({ id: 'f800600f-63dc-4651-b612-84021c6f13c7' })).toBe(
      'resume-f800600f.pdf'
    );
  });

  it('final fallback is a plain kind name', () => {
    expect(buildResumeFilename({})).toBe('resume.pdf');
    expect(buildResumeFilename({ kind: 'cover-letter' })).toBe('cover_letter.pdf');
  });
});
