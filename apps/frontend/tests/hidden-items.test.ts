import { describe, expect, it } from 'vitest';
import { stripHiddenItems, countHiddenItems } from '@/lib/utils/hidden-items';
import type { ResumeData } from '@/components/dashboard/resume-component';

/**
 * Per-item visibility: a user tailoring for one application hides the jobs and
 * projects that do not apply, WITHOUT deleting them from their record. The flag
 * therefore lives on the saved item and only the rendered document omits it.
 */
const data: ResumeData = {
  workExperience: [
    { id: 1, title: 'Kept job' },
    { id: 2, title: 'Hidden job', hidden: true },
  ],
  education: [
    { id: 1, institution: 'Kept school' },
    { id: 2, institution: 'Hidden school', hidden: true },
  ],
  personalProjects: [
    { id: 1, name: 'Kept project', hidden: false },
    { id: 2, name: 'Hidden project', hidden: true },
  ],
  customSections: {
    awards: {
      sectionType: 'itemList',
      items: [
        { id: 1, title: 'Kept award' },
        { id: 2, title: 'Hidden award', hidden: true },
      ],
    },
    note: { sectionType: 'text', text: 'no items here' },
  },
};

describe('stripHiddenItems', () => {
  it('drops hidden entries from every item-bearing section', () => {
    const out = stripHiddenItems(data);
    expect(out.workExperience?.map((i) => i.title)).toEqual(['Kept job']);
    expect(out.education?.map((i) => i.institution)).toEqual(['Kept school']);
    expect(out.personalProjects?.map((i) => i.name)).toEqual(['Kept project']);
    expect(out.customSections?.awards.items?.map((i) => i.title)).toEqual(['Kept award']);
  });

  it('does not mutate the input, so the saved resume keeps the hidden entries', () => {
    stripHiddenItems(data);
    expect(data.workExperience).toHaveLength(2);
    expect(data.personalProjects).toHaveLength(2);
    expect(data.customSections?.awards.items).toHaveLength(2);
  });

  it('leaves a non-item custom section untouched', () => {
    const out = stripHiddenItems(data);
    expect(out.customSections?.note).toEqual({ sectionType: 'text', text: 'no items here' });
  });

  it('omits absent keys rather than inventing empty arrays', () => {
    // A template that sees `education: []` may render a stray heading with
    // nothing under it, so an absent section must stay absent.
    const out = stripHiddenItems({ workExperience: [{ id: 1, title: 'Only job' }] });
    expect('education' in out).toBe(false);
    expect('personalProjects' in out).toBe(false);
    expect('customSections' in out).toBe(false);
  });

  it('is a no-op when nothing is hidden', () => {
    const clean: ResumeData = { workExperience: [{ id: 1, title: 'A' }] };
    expect(stripHiddenItems(clean)).toEqual(clean);
  });
});

describe('countHiddenItems', () => {
  it('counts hidden entries across all sections', () => {
    expect(countHiddenItems(data)).toBe(4);
  });

  it('is zero for a resume with nothing hidden', () => {
    expect(countHiddenItems({ workExperience: [{ id: 1, title: 'A' }] })).toBe(0);
  });

  it('is zero for an empty resume', () => {
    expect(countHiddenItems({})).toBe(0);
  });
});
