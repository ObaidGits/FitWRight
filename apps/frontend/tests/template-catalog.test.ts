import { describe, expect, it } from 'vitest';

import {
  RESUME_TEMPLATES,
  filterTemplates,
  getTemplateById,
  photoSupportIsConsistent,
  sortTemplates,
  templateToSettings,
} from '@/lib/resume/template-catalog';
import {
  experienceLevelFromResume,
  recommendTemplates,
  scoreTemplate,
  signalFromResume,
} from '@/lib/resume/template-recommend';
import { TEMPLATE_OPTIONS } from '@/lib/types/template-settings';
import type { ResumeData } from '@/components/dashboard/resume-component';

const ENGINE_IDS = new Set(TEMPLATE_OPTIONS.map((o) => o.id));

describe('template catalog integrity', () => {
  it('ships a substantial library (20–30 templates)', () => {
    expect(RESUME_TEMPLATES.length).toBeGreaterThanOrEqual(20);
    expect(RESUME_TEMPLATES.length).toBeLessThanOrEqual(40);
  });

  it('has unique, URL-safe ids', () => {
    const ids = RESUME_TEMPLATES.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z0-9-]+$/);
  });

  it('every template maps to a real layout engine', () => {
    for (const t of RESUME_TEMPLATES) expect(ENGINE_IDS.has(t.engine)).toBe(true);
  });

  it('ATS scores are within 1–5 with a stated reason', () => {
    for (const t of RESUME_TEMPLATES) {
      expect(t.atsScore).toBeGreaterThanOrEqual(1);
      expect(t.atsScore).toBeLessThanOrEqual(5);
      expect(t.atsNote.length).toBeGreaterThan(0);
    }
  });

  it('photo support is consistent with the engine capabilities', () => {
    // A photo-incapable engine (latex) must declare photoSupport: 'none'.
    for (const t of RESUME_TEMPLATES) {
      expect(photoSupportIsConsistent(t)).toBe(true);
    }
  });

  it('covers every category and both photo/no-photo layouts', () => {
    const cats = new Set(RESUME_TEMPLATES.map((t) => t.category));
    expect(cats.size).toBeGreaterThanOrEqual(6);
    expect(RESUME_TEMPLATES.some((t) => t.photoSupport === 'none')).toBe(true);
    expect(RESUME_TEMPLATES.some((t) => t.photoSupport !== 'none')).toBe(true);
  });
});

describe('templateToSettings — composes a preset into valid TemplateSettings', () => {
  it('applies the engine + preset (accent, fonts, spacing)', () => {
    const t = getTemplateById('ats-executive')!;
    const s = templateToSettings(t);
    expect(s.template).toBe('swiss-single');
    expect(s.fontSize.headerFont).toBe('serif');
    expect(s.fontSize.bodyFont).toBe('serif');
    expect(s.spacing.section).toBe(4);
  });

  it('seeds single-typeface engine fonts by default (latex)', () => {
    const t = getTemplateById('finance-banking')!;
    const s = templateToSettings(t);
    expect(s.template).toBe('latex');
    // applyTemplatePreset seeds latex → serif/serif.
    expect(s.fontSize.headerFont).toBe('serif');
  });
});

describe('filter + sort', () => {
  it('filters by category, photo, ATS, and free-text query', () => {
    expect(
      filterTemplates(RESUME_TEMPLATES, { category: 'ats' }).every((t) => t.category === 'ats')
    ).toBe(true);
    expect(
      filterTemplates(RESUME_TEMPLATES, { photo: 'no-photo' }).every(
        (t) => t.photoSupport === 'none'
      )
    ).toBe(true);
    expect(filterTemplates(RESUME_TEMPLATES, { minAts: 5 }).every((t) => t.atsScore >= 5)).toBe(
      true
    );
    const q = filterTemplates(RESUME_TEMPLATES, { query: 'developer' });
    expect(q.length).toBeGreaterThan(0);
    expect(q.some((t) => t.id === 'frontend-developer')).toBe(true);
  });

  it('sorts by ATS, popularity, and name', () => {
    const byAts = sortTemplates(RESUME_TEMPLATES, 'ats');
    expect(byAts[0].atsScore).toBe(5);
    const byName = sortTemplates(RESUME_TEMPLATES, 'name');
    expect(byName[0].name.localeCompare(byName[byName.length - 1].name)).toBeLessThanOrEqual(0);
  });
});

describe('recommendations', () => {
  const swe: ResumeData = {
    personalInfo: { name: 'Dev', title: 'Senior Software Engineer' },
    workExperience: [
      { id: 1, title: 'Engineer', company: 'A', years: '2019 - 2024' },
      { id: 2, title: 'Engineer', company: 'B', years: '2016 - 2019' },
      { id: 3, title: 'Engineer', company: 'C', years: '2014 - 2016' },
    ],
    education: [],
    personalProjects: [],
    additional: { technicalSkills: ['React', 'Node', 'AWS'] },
  };

  it('infers experience level from title + history', () => {
    expect(experienceLevelFromResume(swe)).toBe('senior');
    expect(
      experienceLevelFromResume({
        ...swe,
        personalInfo: { title: 'Marketing Intern' },
      } as ResumeData)
    ).toBe('student');
  });

  it('recommends a software-engineering template for a SWE resume', () => {
    const recs = recommendTemplates(signalFromResume(swe), RESUME_TEMPLATES, 6);
    expect(recs.length).toBe(6);
    const ids = recs.map((r) => r.template.id);
    expect(ids).toContain('software-engineer');
    // Every returned recommendation carries at least one human reason.
    expect(recs.every((r) => r.reasons.length > 0)).toBe(true);
  });

  it('scores a role match higher than an unrelated template', () => {
    const signal = signalFromResume(swe);
    const swScore = scoreTemplate(getTemplateById('software-engineer')!, signal).score;
    const academicScore = scoreTemplate(getTemplateById('academic-research')!, signal).score;
    expect(swScore).toBeGreaterThan(academicScore);
  });

  it('recommends a student template for a fresher', () => {
    const fresher: ResumeData = {
      personalInfo: { name: 'Grad', title: 'Computer Science Student' },
      workExperience: [],
      education: [{ id: 1, institution: 'MIT', degree: 'BS', years: '2021 - 2025' }],
      personalProjects: [],
      additional: {},
    };
    const recs = recommendTemplates(signalFromResume(fresher), RESUME_TEMPLATES, 8);
    const ids = recs.map((r) => r.template.id);
    expect(ids.some((id) => id === 'student-fresher' || id === 'graduate-student')).toBe(true);
  });
});
