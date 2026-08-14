import type { ResumeData, CustomSection } from '@/components/dashboard/resume-component';

/**
 * Drop items the user has hidden, just before the resume is rendered.
 *
 * Hiding is a tailoring tool: for this application you want the two relevant
 * jobs, not all five, and deleting the other three to achieve that would lose
 * them for every future application. So the flag lives on the item and the
 * item stays in the saved resume - only the *rendered* document omits it.
 *
 * This MUST be applied on every path that produces a document, or the preview
 * and the PDF disagree. There are exactly two such paths and both call this:
 *   - the builder's preview memo (what the user sees while editing),
 *   - app/print/resumes/[id] (what headless Chromium turns into the PDF).
 * The PDF is rendered by loading that print route, so filtering there covers
 * the export without any backend involvement.
 *
 * Pure and non-mutating: it returns a shallow copy with filtered arrays, and
 * omits a key entirely when it was absent, so it cannot introduce empty arrays
 * that a template might render as a stray heading.
 */
export function stripHiddenItems(data: ResumeData): ResumeData {
  if (!data) return data;

  const out: ResumeData = { ...data };

  if (data.workExperience) {
    out.workExperience = data.workExperience.filter((item) => !item.hidden);
  }
  if (data.education) {
    out.education = data.education.filter((item) => !item.hidden);
  }
  if (data.personalProjects) {
    out.personalProjects = data.personalProjects.filter((item) => !item.hidden);
  }

  if (data.customSections) {
    const sections: Record<string, CustomSection> = {};
    for (const [key, section] of Object.entries(data.customSections)) {
      sections[key] = section.items
        ? { ...section, items: section.items.filter((item) => !item.hidden) }
        : section;
    }
    out.customSections = sections;
  }

  return out;
}

/** How many items are hidden, for telling the user what the PDF will leave out. */
export function countHiddenItems(data: ResumeData): number {
  if (!data) return 0;
  const lists = [data.workExperience, data.education, data.personalProjects];
  let count = lists.reduce(
    (total, list) => total + (list?.filter((item) => item.hidden).length ?? 0),
    0
  );
  for (const section of Object.values(data.customSections ?? {})) {
    count += section.items?.filter((item) => item.hidden).length ?? 0;
  }
  return count;
}
