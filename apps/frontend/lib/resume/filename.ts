/**
 * Meaningful, filesystem-safe PDF download names.
 *
 * Downloads previously used the resume UUID (e.g. `tailored-resume-<uuid>.pdf`),
 * which is opaque and long. This builds a short, human-readable name from the
 * data we actually have - the person's name plus the target company or their
 * role - degrading gracefully and always ending in `.pdf`.
 *
 * Examples:
 *   { name: 'Obaid Zeeshan', company: 'Acme' }          -> Obaid_Zeeshan_Acme_Resume.pdf
 *   { name: 'Obaid Zeeshan', role: 'Full Stack Dev' }   -> Obaid_Zeeshan_Full_Stack_Dev_Resume.pdf
 *   { name: 'Obaid Zeeshan' }                            -> Obaid_Zeeshan_Resume.pdf
 *   { id: 'f800600f-...' }                               -> resume-f800600f.pdf
 */

/** Slugify one filename part: strip accents/illegal chars, collapse to `_`. */
export function slugifyNamePart(value: string | null | undefined, maxLen = 40): string {
  return (value ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '') // drop combining accents
    .replace(/[^\p{L}\p{N}]+/gu, '_') // any non-alphanumeric -> underscore
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, maxLen)
    .replace(/_+$/g, '');
}

export interface ResumeFilenameParts {
  name?: string | null;
  /** Target company (preferred tail when known). */
  company?: string | null;
  /** The person's role/title (tail fallback when no company). */
  role?: string | null;
  /** Resume id, used only for the last-resort fallback. */
  id?: string | null;
  kind?: 'resume' | 'cover-letter' | 'interview-prep';
}

/** Build a short, meaningful `<...>.pdf` filename from the available fields. */
export function buildResumeFilename(parts: ResumeFilenameParts): string {
  const kindLabel =
    parts.kind === 'cover-letter'
      ? 'Cover_Letter'
      : parts.kind === 'interview-prep'
        ? 'Interview_Prep'
        : 'Resume';
  const name = slugifyNamePart(parts.name, 40);
  const tail = slugifyNamePart(parts.company, 30) || slugifyNamePart(parts.role, 30);

  const segments = [name, tail].filter(Boolean);
  let base: string;
  if (segments.length > 0) {
    // Always append the kind so the file's purpose is obvious.
    base = [...segments, kindLabel].join('_');
  } else if (parts.id) {
    base = `${kindLabel.toLowerCase()}-${parts.id.slice(0, 8)}`;
  } else {
    base = kindLabel.toLowerCase();
  }

  // Keep the whole thing comfortably short for every filesystem.
  base = base.slice(0, 90).replace(/_+$/g, '');
  return `${base}.pdf`;
}
