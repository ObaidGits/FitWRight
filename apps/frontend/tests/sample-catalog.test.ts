import { describe, expect, it } from 'vitest';

import {
  RESUME_SAMPLES,
  filterSamples,
  getSampleById,
  relatedSamples,
} from '@/lib/resume/sample-catalog';
import { getTemplateById } from '@/lib/resume/template-catalog';

describe('resume sample catalog integrity', () => {
  it('ships a library of samples', () => {
    expect(RESUME_SAMPLES.length).toBeGreaterThanOrEqual(10);
  });

  it('has unique, URL-safe ids', () => {
    const ids = RESUME_SAMPLES.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z0-9-]+$/);
  });

  it('every sample has realistic, complete content', () => {
    for (const s of RESUME_SAMPLES) {
      expect(s.data.personalInfo?.name).toBeTruthy();
      expect(s.data.summary && s.data.summary.length).toBeGreaterThan(20);
      expect((s.data.workExperience ?? []).length).toBeGreaterThanOrEqual(1);
      expect((s.data.additional?.technicalSkills ?? []).length).toBeGreaterThanOrEqual(1);
      expect(s.atsScore).toBeGreaterThanOrEqual(1);
      expect(s.atsScore).toBeLessThanOrEqual(5);
    }
  });

  it('every sample recommends a real template', () => {
    for (const s of RESUME_SAMPLES) {
      expect(getTemplateById(s.recommendedTemplateId)).toBeDefined();
    }
  });

  it('covers several industries and both photo/no-photo variants', () => {
    const industries = new Set(RESUME_SAMPLES.map((s) => s.industry));
    expect(industries.size).toBeGreaterThanOrEqual(5);
    expect(RESUME_SAMPLES.some((s) => s.hasPhoto)).toBe(true);
    expect(RESUME_SAMPLES.some((s) => !s.hasPhoto)).toBe(true);
  });

  it('filters by query and category', () => {
    const q = filterSamples(RESUME_SAMPLES, { query: 'react' });
    expect(q.some((s) => s.id === 'frontend-developer')).toBe(true);
    const tech = filterSamples(RESUME_SAMPLES, { category: 'technology' });
    expect(tech.every((s) => s.category === 'technology')).toBe(true);
  });

  it('related samples share the category and exclude self', () => {
    const swe = getSampleById('software-engineer')!;
    const rel = relatedSamples(swe);
    expect(rel.every((r) => r.category === swe.category && r.id !== swe.id)).toBe(true);
  });
});
